from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from accounts.managers import UserManager
from core.models import TimeStampedModel


class User(AbstractUser):
    class AccountStatus(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        ACTIVE = 'active', 'Activa'
        SUSPENDED = 'suspended', 'Suspendida'
        DEACTIVATED = 'deactivated', 'Desactivada'

    class AuthSource(models.TextChoices):
        LOCAL = 'local', 'Local'
        EXTERNAL = 'external', 'Externa'

    username = models.CharField(
        'nombre de usuario',
        max_length=150,
        null=True,
        blank=True,
        unique=True,
    )
    email = models.EmailField('email', unique=True)
    first_name = models.CharField('nombre', max_length=150)
    last_name = models.CharField('apellidos', max_length=150, blank=True)
    nif_nie = models.CharField('NIF/NIE', max_length=32, blank=True)
    phone = models.CharField('telefono', max_length=32, blank=True)
    habitual_recycling_center = models.ForeignKey(
        'locations.RecyclingCenter',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='habitual_users',
    )
    account_status = models.CharField(
        max_length=20,
        choices=AccountStatus.choices,
        default=AccountStatus.PENDING,
        db_index=True,
    )
    external_auth_id = models.CharField(max_length=255, null=True, blank=True, unique=True)
    auth_source = models.CharField(
        max_length=16,
        choices=AuthSource.choices,
        default=AuthSource.LOCAL,
        db_index=True,
    )
    must_change_password = models.BooleanField(default=False)
    preferences = models.JSONField(default=dict, blank=True)
    consent_version = models.CharField(max_length=50, blank=True)
    consent_accepted_at = models.DateTimeField(null=True, blank=True)
    reconciled_from_user = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reconciled_accounts',
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name']

    objects = UserManager()

    class Meta:
        permissions = [
            ('manage_staff', 'Puede gestionar miembros del equipo'),
        ]
        constraints = [
            models.UniqueConstraint(Lower('email'), name='accounts_user_email_ci_unique'),
            models.UniqueConstraint(Lower('username'), name='accounts_user_username_ci_unique'),
        ]
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['account_status']),
            models.Index(fields=['auth_source']),
        ]

    def __str__(self):
        return self.email


class MagicLoginToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='magic_login_tokens',
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)
    redirect_path = models.CharField(max_length=2048, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'used_at', 'expires_at']),
        ]

    def __str__(self):
        return f'Magic link {self.pk} · {self.user}'


class UserCenterAccess(TimeStampedModel):
    class ScopeRole(models.TextChoices):
        OPERATOR = 'operator', 'Operador de punto limpio'
        SUPERVISOR = 'supervisor', 'Supervisor de punto limpio'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='center_accesses')
    center = models.ForeignKey('locations.RecyclingCenter', on_delete=models.CASCADE, related_name='staff_accesses')
    role = models.CharField(max_length=20, choices=ScopeRole.choices)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'center', 'role'], name='unique_user_center_role')
        ]
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['center', 'role', 'is_active']),
        ]

    def __str__(self):
        return f'{self.user} - {self.center} ({self.role})'
