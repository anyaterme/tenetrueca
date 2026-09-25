from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum

from audit.models import AuditEvent
from points.models import PointMovement


REGISTRATION_POINTS = 100
ACCEPTED_OBJECT_POINTS = 100
PICKUP_POINTS = 50

EXPECTED_AMOUNTS = {
    PointMovement.Reason.REGISTRATION: REGISTRATION_POINTS,
    PointMovement.Reason.OBJECT_ACCEPTED: ACCEPTED_OBJECT_POINTS,
    PointMovement.Reason.OBJECT_PICKED_UP: -PICKUP_POINTS,
}


class InsufficientPoints(ValidationError):
    pass


def points_balance(user):
    return user.point_movements.aggregate(total=Sum('amount'))['total'] or 0


def _locked_user(user):
    return get_user_model().objects.select_for_update().get(pk=user.pk)


@transaction.atomic
def ensure_sufficient_points(*, user, required=PICKUP_POINTS):
    locked_user = _locked_user(user)
    balance = points_balance(locked_user)
    if balance < required:
        raise InsufficientPoints(
            f'No tienes saldo suficiente. Necesitas {required} puntos y dispones de {balance}.'
        )
    return balance


@transaction.atomic
def create_point_movement(*, user, amount, reason, reference, operation=None, actor=None):
    if EXPECTED_AMOUNTS.get(reason) != amount:
        raise ValidationError('La cantidad no corresponde al motivo del movimiento.')
    locked_user = _locked_user(user)
    existing = PointMovement.objects.filter(reference=reference).first()
    if existing is not None:
        if existing.user_id != locked_user.pk or existing.amount != amount or existing.reason != reason:
            raise ValidationError('La referencia de puntos ya está asociada a otro movimiento.')
        return existing, False

    balance_before = points_balance(locked_user)
    balance_after = balance_before + amount
    if balance_after < 0:
        raise InsufficientPoints(
            f'No tienes saldo suficiente. Necesitas {-amount} puntos y dispones de {balance_before}.'
        )

    try:
        with transaction.atomic():
            movement = PointMovement.objects.create(
                user=locked_user,
                amount=amount,
                reason=reason,
                reference=reference,
                operation=operation,
            )
    except IntegrityError:
        movement = PointMovement.objects.get(reference=reference)
        if movement.user_id != locked_user.pk or movement.amount != amount or movement.reason != reason:
            raise ValidationError('La referencia de puntos ya está asociada a otro movimiento.')
        return movement, False

    AuditEvent.objects.create(
        actor=actor or locked_user,
        action='points.movement.created',
        entity='PointMovement',
        entity_id=str(movement.pk),
        source='service',
        before={'balance': balance_before},
        after={'balance': balance_after, 'amount': amount},
        metadata={
            'user_id': locked_user.pk,
            'reason': reason,
            'reference': reference,
            'operation_id': operation.pk if operation else None,
        },
    )
    return movement, True


def award_registration_points(*, user):
    return create_point_movement(
        user=user,
        amount=REGISTRATION_POINTS,
        reason=PointMovement.Reason.REGISTRATION,
        reference=f'account-registration:{user.pk}',
        actor=user,
    )


def award_accepted_object_points(*, inventory_item, operation, actor):
    return create_point_movement(
        user=inventory_item.owner,
        amount=ACCEPTED_OBJECT_POINTS,
        reason=PointMovement.Reason.OBJECT_ACCEPTED,
        reference=f'object-accepted:{inventory_item.pk}',
        operation=operation,
        actor=actor,
    )


def charge_pickup_points(*, reservation, operation, actor):
    return create_point_movement(
        user=reservation.user,
        amount=-PICKUP_POINTS,
        reason=PointMovement.Reason.OBJECT_PICKED_UP,
        reference=f'reservation-pickup:{reservation.pk}',
        operation=operation,
        actor=actor,
    )
