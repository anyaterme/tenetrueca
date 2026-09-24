from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='audit_events',
    )
    action = models.CharField(max_length=120, db_index=True)
    entity = models.CharField(max_length=120, db_index=True)
    entity_id = models.CharField(max_length=120, db_index=True)
    source = models.CharField(max_length=80, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    result = models.CharField(max_length=40, default='success', db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['entity', 'entity_id']),
            models.Index(fields=['action', 'created_at']),
        ]

    def __str__(self):
        return f'{self.action} {self.entity}:{self.entity_id}'
