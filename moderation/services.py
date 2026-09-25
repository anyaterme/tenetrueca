from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from core.permissions import user_can_moderate
from moderation.models import ModerationDecision
from publications.models import Publication


DECISION_STATUS = {
    ModerationDecision.Decision.REQUEST_CHANGES: Publication.Status.CHANGES_REQUESTED,
    ModerationDecision.Decision.APPROVE: Publication.Status.APPROVED,
    ModerationDecision.Decision.REJECT: Publication.Status.REJECTED,
}


@transaction.atomic
def moderate_publication(*, publication_id, reviewer, decision, notes=''):
    if not user_can_moderate(reviewer):
        raise PermissionDenied
    if decision not in DECISION_STATUS:
        raise ValidationError('Selecciona una decisión válida.')

    notes = notes.strip()
    if decision in {
        ModerationDecision.Decision.REQUEST_CHANGES,
        ModerationDecision.Decision.REJECT,
    } and not notes:
        raise ValidationError('La decisión requiere un motivo.')

    publication = Publication.objects.select_for_update().get(pk=publication_id)
    if publication.status != Publication.Status.PENDING_REVIEW:
        raise ValidationError('Esta publicación ya no está pendiente de revisión.')

    previous_status = publication.status
    resulting_status = DECISION_STATUS[decision]
    decided_at = timezone.now()
    moderation_decision = ModerationDecision.objects.create(
        publication=publication,
        reviewer=reviewer,
        decision=decision,
        previous_status=previous_status,
        resulting_status=resulting_status,
        reason_code=(
            'changes_required'
            if decision == ModerationDecision.Decision.REQUEST_CHANGES
            else 'content_rejected'
            if decision == ModerationDecision.Decision.REJECT
            else ''
        ),
        notes=notes,
        decided_at=decided_at,
    )

    publication.status = resulting_status
    publication.reviewed_at = decided_at
    publication.status_changed_at = decided_at
    publication.approved_at = (
        decided_at if resulting_status == Publication.Status.APPROVED else None
    )
    publication.save(
        update_fields=[
            'status',
            'reviewed_at',
            'status_changed_at',
            'approved_at',
            'updated_at',
        ]
    )

    AuditEvent.objects.create(
        actor=reviewer,
        action=f'publication.moderation.{decision}',
        entity='Publication',
        entity_id=str(publication.pk),
        source='backoffice',
        before={'status': previous_status},
        after={'status': resulting_status},
        metadata={
            'decision_id': moderation_decision.pk,
            'reason_code': moderation_decision.reason_code,
            'notes': notes,
        },
    )
    return publication, moderation_decision
