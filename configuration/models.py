from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class FunctionalRule(TimeStampedModel):
    class ValueType(models.TextChoices):
        INTEGER = 'integer', 'Entero'
        DECIMAL = 'decimal', 'Decimal'
        BOOLEAN = 'boolean', 'Booleano'
        TEXT = 'text', 'Texto'
        JSON = 'json', 'JSON'

    key = models.SlugField(max_length=120)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    value_type = models.CharField(max_length=20, choices=ValueType.choices)
    value = models.JSONField()
    version = models.PositiveIntegerField(default=1)
    effective_from = models.DateTimeField(default=timezone.now)
    effective_to = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ['key', '-effective_from']
        constraints = [
            models.UniqueConstraint(fields=['key', 'version'], name='unique_functional_rule_version')
        ]
        indexes = [
            models.Index(fields=['key', 'is_active']),
            models.Index(fields=['effective_from', 'effective_to']),
        ]

    def __str__(self):
        return f'{self.key} v{self.version}'
