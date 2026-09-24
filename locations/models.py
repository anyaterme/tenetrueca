from django.db import models

from core.models import ActiveModel


class RecyclingCenter(ActiveModel):
    class OperationalStatus(models.TextChoices):
        OPEN = 'open', 'Operativo'
        TEMPORARILY_CLOSED = 'temporarily_closed', 'Cierre temporal'
        CLOSED = 'closed', 'Cerrado'

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    address = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    schedule = models.TextField(blank=True)
    services = models.JSONField(default=list, blank=True)
    operational_status = models.CharField(
        max_length=32,
        choices=OperationalStatus.choices,
        default=OperationalStatus.OPEN,
        db_index=True,
    )
    public_information = models.TextField(blank=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['operational_status', 'is_active']),
        ]

    def __str__(self):
        return self.name
