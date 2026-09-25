from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from inventory.models import InventoryItem


class Reservation(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Activa'
        EXPIRED = 'expired', 'Caducada'
        CANCELLED = 'cancelled', 'Cancelada'
        COMPLETED = 'completed', 'Completada'

    inventory_item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name='reservations',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='inventory_reservations',
    )
    reserved_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-reserved_at']
        indexes = [
            models.Index(fields=['user', 'status', 'expires_at']),
            models.Index(fields=['inventory_item', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['inventory_item'],
                condition=models.Q(status='active'),
                name='reservations_one_active_per_item',
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F('reserved_at')),
                name='reservations_expiry_after_start',
            ),
        ]

    def clean(self):
        super().clean()
        if self.expires_at and self.reserved_at and self.expires_at <= self.reserved_at:
            raise ValidationError({'expires_at': 'La caducidad debe ser posterior a la reserva.'})

        if self.status != self.Status.ACTIVE or not self.inventory_item_id:
            return

        item_status = (
            InventoryItem.objects.filter(pk=self.inventory_item_id)
            .values_list('status', flat=True)
            .first()
        )
        allowed_statuses = {InventoryItem.Status.AVAILABLE}
        if not self._state.adding:
            allowed_statuses.add(InventoryItem.Status.RESERVED)
        if item_status not in allowed_statuses:
            raise ValidationError(
                {'inventory_item': 'Solo se pueden mantener reservas activas sobre objetos disponibles o reservados.'}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.inventory_item} · {self.user}'
