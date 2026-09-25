from datetime import timedelta
from io import BytesIO
from uuid import UUID, uuid4

import qrcode
from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEvent
from catalog.models import catalog_readiness_errors
from core.permissions import allowed_reception_centers
from inventory.models import InventoryItem
from operations.models import Operation
from points.services import charge_pickup_points, ensure_sufficient_points
from reservations.models import Reservation


class InvalidReservationToken(ValidationError):
    pass


def reservation_duration():
    hours = getattr(settings, 'RESERVATION_EXPIRATION_HOURS', 72)
    return timedelta(hours=hours)


def _reservation_reference(reservation, action):
    prefix = 'PCK' if action == Operation.Type.PICKUP else 'RSV'
    return f'{prefix}-{reservation.pk:08d}-{uuid4().hex[:8]}'


def _record_audit(*, actor, action, reservation, source, before=None, after=None, result='success', metadata=None):
    AuditEvent.objects.create(
        actor=actor,
        action=action,
        entity='Reservation',
        entity_id=str(reservation.public_id),
        source=source,
        before=before,
        after=after,
        result=result,
        metadata={
            'reservation_id': reservation.pk,
            'inventory_item_id': reservation.inventory_item_id,
            'center_id': reservation.inventory_item.center_id,
            **(metadata or {}),
        },
    )


def _record_operation(*, actor, reservation, operation_type, status, notes='', metadata=None):
    return Operation.objects.create(
        reference=_reservation_reference(reservation, operation_type),
        operation_type=operation_type,
        status=status,
        actor=actor,
        reusable_object=reservation.inventory_item,
        center=reservation.inventory_item.center,
        notes=notes,
        metadata={
            'reservation_public_id': str(reservation.public_id),
            **(metadata or {}),
        },
    )


def _item_can_return_to_catalog(item):
    if item.publication_id is None:
        return False
    if item.received_at is None or item.validated_at is None or item.validated_by_id is None:
        return False
    if item.category_node_id is None or item.category_node_id != item.publication.category_id:
        return False
    return not catalog_readiness_errors(item.publication, item.center)


def _active_qs():
    return Reservation.objects.select_related(
        'inventory_item',
        'inventory_item__publication',
        'inventory_item__publication__category',
        'inventory_item__center',
        'user',
    )


@transaction.atomic
def create_reservation(*, inventory_item_id, user):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied

    item = (
        InventoryItem.objects.select_for_update(of=('self',))
        .select_related('publication', 'publication__category', 'center')
        .get(pk=inventory_item_id)
    )
    publication_owner_id = item.publication.submitter_id if item.publication_id else None
    if user.pk in {item.owner_id, publication_owner_id}:
        raise ValidationError('No puedes reservar un objeto que has publicado.')
    if not InventoryItem.objects.public_catalog().filter(pk=item.pk).exists():
        raise ValidationError('Este objeto ya no está disponible para reserva.')
    if Reservation.objects.filter(
        inventory_item=item,
        status=Reservation.Status.ACTIVE,
    ).exists():
        raise ValidationError('Este objeto ya tiene una reserva activa.')

    ensure_sufficient_points(user=user)

    now = timezone.now()
    reservation = Reservation(
        inventory_item=item,
        user=user,
        reserved_at=now,
        expires_at=now + reservation_duration(),
    )
    try:
        reservation.save()
    except IntegrityError as error:
        raise ValidationError('Este objeto ya tiene una reserva activa.') from error

    previous_status = item.status
    item.status = InventoryItem.Status.RESERVED
    item.save(update_fields=['status', 'updated_at'])
    _record_audit(
        actor=user,
        action='reservations.created',
        reservation=reservation,
        source='web',
        before={'item_status': previous_status},
        after={'status': reservation.status, 'item_status': item.status},
    )
    _record_operation(
        actor=user,
        reservation=reservation,
        operation_type=Operation.Type.RESERVATION,
        status=Operation.Status.COMPLETED,
        notes='Reserva creada.',
    )
    return reservation


@transaction.atomic
def cancel_reservation(*, reservation_public_id, user):
    reservation = (
        _active_qs()
        .select_for_update(of=('self',))
        .select_related('inventory_item__category_node')
        .get(public_id=reservation_public_id)
    )
    if reservation.user_id != user.pk:
        raise PermissionDenied
    if reservation.status != Reservation.Status.ACTIVE:
        raise ValidationError('Solo se pueden cancelar reservas activas.')

    now = timezone.now()
    item = InventoryItem.objects.select_for_update().get(pk=reservation.inventory_item_id)
    previous_status = item.status
    reservation.status = Reservation.Status.CANCELLED
    reservation.cancelled_at = now
    reservation.invalidate_qr(now)
    reservation.save(update_fields=['status', 'cancelled_at', 'qr_invalidated_at', 'updated_at'])

    released = False
    item = (
        InventoryItem.objects.select_related('publication', 'publication__category', 'center', 'category_node')
        .get(pk=item.pk)
    )
    if item.status == InventoryItem.Status.RESERVED and _item_can_return_to_catalog(item):
        item.status = InventoryItem.Status.AVAILABLE
        item.save(update_fields=['status', 'updated_at'])
        released = True

    _record_audit(
        actor=user,
        action='reservations.cancelled',
        reservation=reservation,
        source='web',
        before={'status': Reservation.Status.ACTIVE, 'item_status': previous_status},
        after={'status': reservation.status, 'item_status': item.status, 'released': released},
    )
    _record_operation(
        actor=user,
        reservation=reservation,
        operation_type=Operation.Type.RESERVATION,
        status=Operation.Status.CANCELLED,
        notes='Reserva cancelada por la persona solicitante.',
        metadata={'released': released},
    )
    return reservation, released


@transaction.atomic
def expire_reservation(*, reservation_id, now=None):
    now = now or timezone.now()
    try:
        reservation = (
            _active_qs()
            .select_for_update(of=('self',))
            .select_related('inventory_item__category_node')
            .get(pk=reservation_id)
        )
    except Reservation.DoesNotExist:
        return None, False
    if reservation.status != Reservation.Status.ACTIVE or reservation.expires_at > now:
        return reservation, False

    item = InventoryItem.objects.select_for_update().get(pk=reservation.inventory_item_id)
    previous_status = item.status
    reservation.status = Reservation.Status.EXPIRED
    reservation.invalidate_qr(now)
    reservation.save(update_fields=['status', 'qr_invalidated_at', 'updated_at'])

    released = False
    item = (
        InventoryItem.objects.select_related('publication', 'publication__category', 'center', 'category_node')
        .get(pk=item.pk)
    )
    if item.status == InventoryItem.Status.RESERVED and _item_can_return_to_catalog(item):
        item.status = InventoryItem.Status.AVAILABLE
        item.save(update_fields=['status', 'updated_at'])
        released = True

    _record_audit(
        actor=None,
        action='reservations.expired',
        reservation=reservation,
        source='management_command',
        before={'status': Reservation.Status.ACTIVE, 'item_status': previous_status},
        after={'status': reservation.status, 'item_status': item.status, 'released': released},
    )
    _record_operation(
        actor=None,
        reservation=reservation,
        operation_type=Operation.Type.RESERVATION,
        status=Operation.Status.CANCELLED,
        notes='Reserva expirada automáticamente.',
        metadata={'released': released},
    )
    return reservation, True


def expire_due_reservations(*, now=None):
    now = now or timezone.now()
    reservation_ids = list(
        Reservation.objects.filter(
            status=Reservation.Status.ACTIVE,
            expires_at__lte=now,
        ).values_list('pk', flat=True)
    )
    expired = 0
    for reservation_id in reservation_ids:
        _, changed = expire_reservation(reservation_id=reservation_id, now=now)
        if changed:
            expired += 1
    return expired


def reservation_from_qr_token(token):
    try:
        payload = signing.loads(token, salt='reservations.pickup_qr')
        qr_identifier = UUID(payload['reservation'])
    except (KeyError, TypeError, ValueError, signing.BadSignature) as error:
        raise InvalidReservationToken('QR inválido.') from error

    try:
        reservation = _active_qs().get(qr_identifier=qr_identifier)
    except Reservation.DoesNotExist as error:
        raise InvalidReservationToken('QR inválido.') from error

    if reservation.status != Reservation.Status.ACTIVE:
        raise InvalidReservationToken('La reserva ya no está activa.')
    if reservation.qr_invalidated_at is not None:
        raise InvalidReservationToken('El QR de esta reserva fue invalidado.')
    if reservation.expires_at <= timezone.now():
        raise InvalidReservationToken('La reserva está caducada.')
    return reservation


def assert_operator_can_pickup(operator, reservation):
    if not allowed_reception_centers(operator).filter(pk=reservation.inventory_item.center_id).exists():
        raise PermissionDenied


def register_pickup_attempt(*, token, operator):
    reservation = reservation_from_qr_token(token)
    assert_operator_can_pickup(operator, reservation)
    _record_audit(
        actor=operator,
        action='reservations.pickup.valid_attempt',
        reservation=reservation,
        source='backoffice',
        metadata={'center_id': reservation.inventory_item.center_id},
    )
    return reservation


@transaction.atomic
def complete_pickup(*, token, operator):
    reservation = reservation_from_qr_token(token)
    assert_operator_can_pickup(operator, reservation)
    reservation = (
        _active_qs()
        .select_for_update(of=('self',))
        .select_related('inventory_item__category_node')
        .get(pk=reservation.pk)
    )
    item = InventoryItem.objects.select_for_update().get(pk=reservation.inventory_item_id)

    if reservation.status == Reservation.Status.COMPLETED:
        return reservation, False
    if reservation.status != Reservation.Status.ACTIVE:
        raise InvalidReservationToken('La reserva ya no está activa.')
    if reservation.qr_invalidated_at is not None or reservation.expires_at <= timezone.now():
        raise InvalidReservationToken('El QR ya no permite completar la recogida.')
    if item.status != InventoryItem.Status.RESERVED:
        raise ValidationError('El objeto no está reservado para recogida.')

    ensure_sufficient_points(user=reservation.user)

    now = timezone.now()
    previous_status = item.status
    item.status = InventoryItem.Status.DELIVERED
    item.save(update_fields=['status', 'updated_at'])
    reservation.status = Reservation.Status.COMPLETED
    reservation.completed_at = now
    reservation.pickup_operator = operator
    reservation.pickup_center = item.center
    reservation.invalidate_qr(now)
    reservation.save(
        update_fields=[
            'status',
            'completed_at',
            'pickup_operator',
            'pickup_center',
            'qr_invalidated_at',
            'updated_at',
        ]
    )
    _record_audit(
        actor=operator,
        action='reservations.pickup.completed',
        reservation=reservation,
        source='backoffice',
        before={'status': Reservation.Status.ACTIVE, 'item_status': previous_status},
        after={
            'status': reservation.status,
            'item_status': item.status,
            'pickup_center_id': item.center_id,
        },
    )
    operation = _record_operation(
        actor=operator,
        reservation=reservation,
        operation_type=Operation.Type.PICKUP,
        status=Operation.Status.COMPLETED,
        notes='Recogida completada por operador autorizado.',
        metadata={'pickup_center_id': item.center_id},
    )
    charge_pickup_points(
        reservation=reservation,
        operation=operation,
        actor=operator,
    )
    return reservation, True


def qr_png_data_uri(value):
    image = qrcode.make(value)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    import base64

    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def reservation_qr_url(request, reservation):
    token = reservation.qr_token()
    path = reverse('reservations:pickup_scan', kwargs={'token': token})
    return request.build_absolute_uri(path)
