import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core import signing
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
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    qr_identifier = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    qr_invalidated_at = models.DateTimeField(null=True, blank=True)
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
    pickup_operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='completed_pickups',
    )
    pickup_center = models.ForeignKey(
        'locations.RecyclingCenter',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='completed_reservation_pickups',
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-reserved_at']
        indexes = [
            models.Index(fields=['user', 'status', 'expires_at']),
            models.Index(fields=['inventory_item', 'status']),
            models.Index(fields=['public_id']),
            models.Index(fields=['qr_identifier']),
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

        item_data = (
            InventoryItem.objects.filter(pk=self.inventory_item_id)
            .values('status', 'owner_id', 'publication__submitter_id')
            .first()
        )
        if self.user_id and item_data and self.user_id in {
            item_data['owner_id'],
            item_data['publication__submitter_id'],
        }:
            raise ValidationError(
                {'user': 'No puedes reservar un objeto que has publicado.'}
            )

        item_status = item_data['status'] if item_data else None
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

    def qr_token(self):
        return signing.dumps(
            {'reservation': self.qr_identifier.hex},
            salt='reservations.pickup_qr',
            compress=True,
        )

    def invalidate_qr(self, when=None):
        if self.qr_invalidated_at is None:
            self.qr_invalidated_at = when or timezone.now()

    def __str__(self):
        return f'{self.inventory_item} · {self.user}'
