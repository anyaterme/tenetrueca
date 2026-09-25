from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class PointMovementQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Los movimientos de puntos no se pueden modificar.')

    def delete(self):
        raise ValidationError('Los movimientos de puntos no se pueden eliminar.')


class PointMovement(models.Model):
    class Reason(models.TextChoices):
        REGISTRATION = 'registration', 'Alta de cuenta'
        OBJECT_ACCEPTED = 'object_accepted', 'Objeto aceptado'
        OBJECT_PICKED_UP = 'object_picked_up', 'Objeto retirado'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='point_movements',
    )
    amount = models.SmallIntegerField()
    reason = models.CharField(max_length=32, choices=Reason.choices, db_index=True)
    reference = models.CharField(max_length=80, unique=True)
    operation = models.ForeignKey(
        'operations.Operation',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='point_movements',
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = PointMovementQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['user', 'created_at'])]
        constraints = [
            models.CheckConstraint(
                condition=~Q(amount=0),
                name='points_movement_amount_nonzero',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Los movimientos de puntos no se pueden modificar.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Los movimientos de puntos no se pueden eliminar.')

    def __str__(self):
        return f'{self.user} · {self.amount:+d} · {self.get_reason_display()}'
