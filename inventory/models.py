from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

from catalog.models import ReusableObject, ReusableObjectQuerySet


class InventoryItemQuerySet(ReusableObjectQuerySet):
    pass


class InventoryItem(ReusableObject):
    objects = InventoryItemQuerySet.as_manager()

    class Meta:
        proxy = True
        verbose_name = 'inventory item'
        verbose_name_plural = 'inventory items'

    def get_absolute_url(self):
        return reverse('catalog:detail', kwargs={'reference': self.reference})


class ReceptionInspection(models.Model):
    class Decision(models.TextChoices):
        ACCEPT = 'accepted', 'Aceptar objeto'
        REJECT = 'rejected', 'Rechazar objeto'

    publication = models.ForeignKey(
        'publications.Publication',
        on_delete=models.PROTECT,
        related_name='reception_inspections',
    )
    center = models.ForeignKey(
        'locations.RecyclingCenter',
        on_delete=models.PROTECT,
        related_name='reception_inspections',
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reception_inspections',
    )
    decision = models.CharField(max_length=16, choices=Decision.choices, db_index=True)
    condition = models.CharField(max_length=24, choices=ReusableObject.Condition.choices)
    internal_location = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    inventory_item = models.ForeignKey(
        'catalog.ReusableObject',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='reception_inspections',
    )
    inspected_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-inspected_at']
        indexes = [
            models.Index(fields=['publication', 'inspected_at']),
            models.Index(fields=['center', 'inspected_at']),
            models.Index(fields=['operator', 'inspected_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['publication'],
                condition=models.Q(decision='accepted'),
                name='inventory_unique_accepted_reception',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(decision='accepted', inventory_item__isnull=False)
                    | models.Q(decision='rejected', inventory_item__isnull=True)
                ),
                name='inventory_reception_matches_item',
            ),
            models.CheckConstraint(
                condition=models.Q(decision='accepted') | ~models.Q(notes=''),
                name='inventory_rejected_reception_has_notes',
            ),
        ]

    def clean(self):
        super().clean()
        if self.decision == self.Decision.REJECT and not self.notes.strip():
            raise ValidationError({'notes': 'Indica el motivo del rechazo.'})
        if self.decision == self.Decision.ACCEPT and self.inventory_item_id:
            if self.inventory_item.publication_id != self.publication_id:
                raise ValidationError({'inventory_item': 'El objeto no pertenece a esta publicación.'})
            if self.inventory_item.center_id != self.center_id:
                raise ValidationError({'center': 'El centro no coincide con el objeto recibido.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.publication} · {self.get_decision_display()} · {self.center}'
