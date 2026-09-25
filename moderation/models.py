from django.conf import settings
from django.db import models
from django.utils import timezone

from publications.models import Publication


class ModerationDecision(models.Model):
    class Decision(models.TextChoices):
        REQUEST_CHANGES = 'request_changes', 'Solicitar cambios'
        APPROVE = 'approve', 'Aprobar'
        REJECT = 'reject', 'Rechazar'

    publication = models.ForeignKey(
        'publications.Publication',
        on_delete=models.PROTECT,
        related_name='moderation_decisions',
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='moderation_decisions',
    )
    decision = models.CharField(max_length=24, choices=Decision.choices)
    previous_status = models.CharField(max_length=24, choices=Publication.Status.choices)
    resulting_status = models.CharField(max_length=24, choices=Publication.Status.choices)
    reason_code = models.CharField(max_length=80, blank=True, db_index=True)
    notes = models.TextField(blank=True)
    decided_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-decided_at']
        indexes = [
            models.Index(fields=['publication', 'decided_at']),
            models.Index(fields=['reviewer', 'decided_at']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(decision='request_changes', resulting_status='changes_requested')
                    | models.Q(decision='approve', resulting_status='approved')
                    | models.Q(decision='reject', resulting_status='rejected')
                ),
                name='moderation_decision_matches_status',
            ),
        ]

    def __str__(self):
        return f'{self.publication} · {self.get_decision_display()}'
