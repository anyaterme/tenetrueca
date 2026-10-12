from django.db import models
from django.core.exceptions import ValidationError
from urllib.parse import urlencode

from core.models import ActiveModel


class RecyclingCenter(ActiveModel):
    class OperationalStatus(models.TextChoices):
        OPEN = 'open', 'Operativo'
        TEMPORARILY_CLOSED = 'temporarily_closed', 'Cierre temporal'
        CLOSED = 'closed', 'Cerrado'

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True, allow_unicode=True)
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
    show_on_public_map = models.BooleanField('Mostrar en el mapa público', default=False, db_index=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['operational_status', 'is_active']),
        ]

    def __str__(self):
        return self.name

    @property
    def has_coordinates(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def directions_url(self):
        destination = f'{self.latitude},{self.longitude}' if self.has_coordinates else f'{self.name}, {self.address}, Tenerife'
        return 'https://www.google.com/maps/dir/?' + urlencode({'api': 1, 'destination': destination})

    def clean(self):
        super().clean()
        errors = {}
        if (self.latitude is None) != (self.longitude is None):
            errors['latitude'] = 'Indica tanto la latitud como la longitud.'
        if self.latitude is not None and not -90 <= self.latitude <= 90:
            errors['latitude'] = 'La latitud debe estar entre -90 y 90.'
        if self.longitude is not None and not -180 <= self.longitude <= 180:
            errors['longitude'] = 'La longitud debe estar entre -180 y 180.'
        if self.show_on_public_map:
            if not self.address.strip():
                errors['address'] = 'Indica la dirección antes de publicar el centro.'
            if not self.has_coordinates:
                errors['show_on_public_map'] = 'Configura las coordenadas antes de publicar el centro.'
        if errors:
            raise ValidationError(errors)
