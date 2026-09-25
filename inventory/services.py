from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from catalog.models import ReusableObject, catalog_readiness_errors
from core.permissions import allowed_reception_centers
from inventory.models import InventoryItem, ReceptionInspection
from operations.models import Operation
from points.services import award_accepted_object_points
from publications.models import Publication


def _inventory_reference(publication):
    reference = f'TT-PUB-{publication.pk:08d}'
    if ReusableObject.objects.filter(reference=reference).exists():
        return f'TT-{publication.pk}-{uuid4().hex[:8]}'
    return reference


def _deposit_operation(*, inventory_item, operator, inspection):
    operation, _ = Operation.objects.get_or_create(
        reference=f'DEP-{inventory_item.pk:08d}',
        defaults={
            'operation_type': Operation.Type.DEPOSIT,
            'status': Operation.Status.COMPLETED,
            'actor': operator,
            'reusable_object': inventory_item,
            'center': inventory_item.center,
            'occurred_at': inspection.inspected_at,
            'notes': 'Objeto recibido y aceptado físicamente.',
            'metadata': {'inspection_id': inspection.pk},
        },
    )
    return operation


@transaction.atomic
def inspect_reception(
    *,
    publication_id,
    operator,
    center,
    decision,
    condition,
    internal_location='',
    notes='',
):
    if not allowed_reception_centers(operator).filter(pk=center.pk).exists():
        raise PermissionDenied
    if decision not in ReceptionInspection.Decision.values:
        raise ValidationError('Selecciona un resultado válido.')

    notes = notes.strip()
    if decision == ReceptionInspection.Decision.REJECT and not notes:
        raise ValidationError('El rechazo requiere un motivo.')

    publication = Publication.objects.select_for_update().select_related(
        'submitter',
        'category',
    ).get(pk=publication_id)
    if publication.status != Publication.Status.APPROVED:
        raise ValidationError('Solo se pueden recibir publicaciones aprobadas.')

    accepted = (
        ReceptionInspection.objects.select_related('inventory_item')
        .filter(
            publication=publication,
            decision=ReceptionInspection.Decision.ACCEPT,
        )
        .first()
    )
    if accepted is not None:
        operation = _deposit_operation(
            inventory_item=accepted.inventory_item,
            operator=accepted.operator,
            inspection=accepted,
        )
        award_accepted_object_points(
            inventory_item=accepted.inventory_item,
            operation=operation,
            actor=accepted.operator,
        )
        return accepted.inventory_item, accepted, False

    inspected_at = timezone.now()
    if decision == ReceptionInspection.Decision.REJECT:
        inspection = ReceptionInspection.objects.create(
            publication=publication,
            center=center,
            operator=operator,
            decision=decision,
            condition=condition,
            internal_location=internal_location,
            notes=notes,
            inspected_at=inspected_at,
        )
        AuditEvent.objects.create(
            actor=operator,
            action='inventory.reception.rejected',
            entity='Publication',
            entity_id=str(publication.pk),
            source='backoffice',
            before={'inventory_item_id': None},
            after={'reception_decision': decision},
            metadata={
                'inspection_id': inspection.pk,
                'center_id': center.pk,
                'condition': condition,
                'notes': notes,
            },
        )
        return None, inspection, True

    readiness_errors = catalog_readiness_errors(publication, center)
    if readiness_errors:
        raise ValidationError(readiness_errors)

    try:
        existing_item = publication.inventory_object
    except ReusableObject.DoesNotExist:
        existing_item = None
    if existing_item is not None:
        raise ValidationError('Esta publicación ya tiene una entrada de inventario.')

    inventory_item = InventoryItem.objects.create(
        reference=_inventory_reference(publication),
        owner=publication.submitter,
        publication=publication,
        center=center,
        title=publication.title,
        description=publication.description,
        category=publication.category.name,
        category_node=publication.category,
        condition=condition,
        status=ReusableObject.Status.AVAILABLE,
        internal_location=internal_location,
        received_at=inspected_at,
        validated_at=inspected_at,
        validated_by=operator,
        validation_notes=notes,
    )
    inventory_item.tags.set(publication.tags.all())
    inspection = ReceptionInspection.objects.create(
        publication=publication,
        center=center,
        operator=operator,
        decision=decision,
        condition=condition,
        internal_location=internal_location,
        notes=notes,
        inventory_item=inventory_item,
        inspected_at=inspected_at,
    )
    AuditEvent.objects.create(
        actor=operator,
        action='inventory.reception.accepted',
        entity='ReusableObject',
        entity_id=str(inventory_item.pk),
        source='backoffice',
        before=None,
        after={
            'status': inventory_item.status,
            'publication_id': publication.pk,
            'center_id': center.pk,
        },
        metadata={
            'inspection_id': inspection.pk,
            'condition': condition,
            'internal_location': internal_location,
            'notes': notes,
        },
    )
    operation = _deposit_operation(
        inventory_item=inventory_item,
        operator=operator,
        inspection=inspection,
    )
    award_accepted_object_points(
        inventory_item=inventory_item,
        operation=operation,
        actor=operator,
    )
    return inventory_item, inspection, True
