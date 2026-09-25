from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class Operation(TimeStampedModel):
    class Type(models.TextChoices):
        PUBLICATION = 'publication', 'Publicación'
        MODERATION = 'moderation', 'Moderación'
        DEPOSIT = 'deposit', 'Depósito'
        RESERVATION = 'reservation', 'Reserva'
        PICKUP = 'pickup', 'Recogida'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        COMPLETED = 'completed', 'Completada'
        FAILED = 'failed', 'Fallida'
        CANCELLED = 'cancelled', 'Cancelada'

    reference = models.CharField(max_length=32, unique=True)
    operation_type = models.CharField(max_length=24, choices=Type.choices, db_index=True)
    status = models.CharField(max_length=24, choices=Status.choices, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='operations',
    )
    reusable_object = models.ForeignKey(
        'catalog.ReusableObject',
        on_delete=models.PROTECT,
        related_name='operations',
    )
    center = models.ForeignKey(
        'locations.RecyclingCenter',
        on_delete=models.PROTECT,
        related_name='operations',
    )
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-occurred_at']
        indexes = [
            models.Index(fields=['operation_type', 'status']),
            models.Index(fields=['center', 'occurred_at']),
        ]

    def __str__(self):
        return f'{self.reference} · {self.get_operation_type_display()}'
