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


class EmailConfiguration(TimeStampedModel):
    class Security(models.TextChoices):
        STARTTLS = 'starttls', 'STARTTLS'
        SSL_TLS = 'ssl_tls', 'SSL/TLS'
        NONE = 'none', 'Ninguna'

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    email_host = models.CharField(max_length=255, null=True, blank=True)
    email_port = models.PositiveIntegerField(null=True, blank=True)
    email_host_user = models.CharField(max_length=255, null=True, blank=True)
    email_host_password_encrypted = models.TextField(null=True, blank=True)
    security = models.CharField(
        max_length=16,
        choices=Security.choices,
        null=True,
        blank=True,
    )
    default_from_email = models.CharField(max_length=320, null=True, blank=True)
    email_timeout = models.PositiveIntegerField(null=True, blank=True)
    updated_by = models.ForeignKey(
        'accounts.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='email_configuration_updates',
    )

    class Meta:
        verbose_name = 'configuración de correo electrónico'
        verbose_name_plural = 'configuración de correo electrónico'
        permissions = [
            ('manage_email_configuration', 'Puede gestionar la configuración de correo'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(pk=1),
                name='configuration_single_email_configuration',
            ),
        ]

    def __str__(self):
        return 'Configuración de correo electrónico'
