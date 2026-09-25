from django.conf import settings
from django.db import models
from django.utils import timezone


class StaffInvitation(models.Model):
    class Role(models.TextChoices):
        ADMINISTRATOR = 'administrator', 'Administrador'
        MANAGER = 'manager', 'Gestor'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='staff_invitations',
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='sent_staff_invitations',
    )
    role = models.CharField(max_length=24, choices=Role.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'accepted_at', 'expires_at']),
        ]

    @property
    def is_pending(self):
        return (
            self.accepted_at is None
            and self.invalidated_at is None
            and self.expires_at > timezone.now()
        )

    def __str__(self):
        return f'Invitación de equipo {self.pk} · {self.user}'


class StaffAuditEvent(models.Model):
    class Action(models.TextChoices):
        INVITED = 'staff.invited', 'Invitación enviada'
        ACCESS_GRANTED = 'staff.access_granted', 'Acceso staff concedido'
        ROLE_CHANGED = 'staff.role_changed', 'Rol modificado'
        ACTIVATED = 'staff.activated', 'Acceso activado'
        DEACTIVATED = 'staff.deactivated', 'Acceso desactivado'
        ACCESS_REMOVED = 'staff.access_removed', 'Acceso staff retirado'
        TEMPORARY_PASSWORD_CREATED = (
            'staff.temporary_password_created',
            'Contraseña temporal generada',
        )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='staff_management_events',
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='staff_audit_events',
    )
    action = models.CharField(max_length=40, choices=Action.choices, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['target_user', 'created_at']),
            models.Index(fields=['action', 'created_at']),
        ]

    def __str__(self):
        return f'{self.get_action_display()} · {self.target_user}'
