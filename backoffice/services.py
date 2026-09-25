import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from backoffice.models import StaffAuditEvent, StaffInvitation
from accounts.models import UserCenterAccess
from configuration.services import EmailConfigurationService
from core.roles import ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER


ROLE_GROUP_NAMES = {
    StaffInvitation.Role.ADMINISTRATOR: ROLE_STAFF_ADMIN,
    StaffInvitation.Role.MANAGER: ROLE_STAFF_MANAGER,
}


def generate_temporary_password(length=20):
    if length < 16:
        raise ValueError('La contraseña temporal debe tener al menos 16 caracteres.')
    character_groups = (
        'ABCDEFGHJKLMNPQRSTUVWXYZ',
        'abcdefghijkmnopqrstuvwxyz',
        '23456789',
        '!@#$%+-_',
    )
    password = [secrets.choice(group) for group in character_groups]
    alphabet = ''.join(character_groups)
    password.extend(secrets.choice(alphabet) for _ in range(length - len(password)))
    secrets.SystemRandom().shuffle(password)
    return ''.join(password)


@dataclass(frozen=True)
class PendingStaffInvitation:
    record: StaffInvitation
    user: object


def staff_role_value(user):
    if (
        user.is_superuser
        or user.has_perm('accounts.manage_staff')
        or user.groups.filter(name=ROLE_STAFF_ADMIN).exists()
    ):
        return StaffInvitation.Role.ADMINISTRATOR
    if user.groups.filter(name=ROLE_STAFF_MANAGER).exists():
        return StaffInvitation.Role.MANAGER
    return ''


def staff_role_label(user):
    value = staff_role_value(user)
    return dict(StaffInvitation.Role.choices).get(value, 'Sin rol')


@transaction.atomic
def assign_staff_role(user, role, *, center=None):
    if role not in StaffInvitation.Role.values:
        raise ValidationError('El rol seleccionado no es válido.')
    if center is not None and not center.is_active:
        raise ValidationError('El punto limpio seleccionado no está activo.')
    manage_staff_permission = Permission.objects.get(
        content_type__app_label='accounts',
        codename='manage_staff',
    )
    role_groups = Group.objects.filter(name__in=ROLE_GROUP_NAMES.values())
    user.groups.remove(*role_groups)
    user.user_permissions.remove(manage_staff_permission)
    user.groups.add(Group.objects.get(name=ROLE_GROUP_NAMES[role]))
    accesses = UserCenterAccess.objects.select_for_update().filter(user=user)
    accesses.filter(is_active=True).update(is_active=False)
    if role == StaffInvitation.Role.MANAGER and center is not None:
        access, _ = UserCenterAccess.objects.get_or_create(
            user=user,
            center=center,
            role=UserCenterAccess.ScopeRole.SUPERVISOR,
            defaults={'is_active': True},
        )
        if not access.is_active:
            access.is_active = True
            access.save(update_fields=['is_active', 'updated_at'])
    for cache_name in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
        if hasattr(user, cache_name):
            delattr(user, cache_name)


def active_administrators():
    User = get_user_model()
    return User.objects.filter(is_staff=True, is_active=True).filter(
        Q(is_superuser=True)
        | Q(groups__name=ROLE_STAFF_ADMIN)
        | Q(
            user_permissions__content_type__app_label='accounts',
            user_permissions__codename='manage_staff',
        )
    ).distinct()


def ensure_admin_safeguards(*, actor, target, operation, new_role=None):
    if target.is_superuser and operation in {'change_role', 'remove'}:
        raise ValidationError(
            'El rol y acceso staff de una cuenta de superusuario se gestionan fuera de este panel.'
        )
    target_is_admin = staff_role_value(target) == StaffInvitation.Role.ADMINISTRATOR
    removes_admin = operation in {'deactivate', 'remove'} or (
        operation == 'change_role'
        and new_role != StaffInvitation.Role.ADMINISTRATOR
    )
    if not target_is_admin or not removes_admin:
        return
    if actor.pk == target.pk:
        if operation == 'deactivate':
            raise ValidationError('No puedes desactivar tu propia cuenta.')
        raise ValidationError('No puedes retirar tus propios privilegios administrativos.')
    if not active_administrators().exclude(pk=target.pk).exists():
        raise ValidationError('El sistema debe conservar al menos un administrador activo.')


def invalidate_user_sessions(user):
    sessions = Session.objects.filter(expire_date__gte=timezone.now())
    for session in sessions.iterator():
        data = session.get_decoded()
        if str(data.get('_auth_user_id', '')) == str(user.pk):
            session.delete()


def record_staff_event(*, actor, target, action, metadata=None):
    return StaffAuditEvent.objects.create(
        actor=actor,
        target_user=target,
        action=action,
        metadata=metadata or {},
    )


class StaffInvitationService:
    TOKEN_BYTES = 32

    @staticmethod
    def hash_token(raw_token):
        return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

    @classmethod
    def issue(cls, *, request, user, invited_by, role):
        now = timezone.now()
        raw_token = secrets.token_urlsafe(cls.TOKEN_BYTES)
        with transaction.atomic():
            StaffInvitation.objects.filter(
                user=user,
                accepted_at__isnull=True,
                invalidated_at__isnull=True,
            ).update(invalidated_at=now)
            invitation = StaffInvitation.objects.create(
                user=user,
                invited_by=invited_by,
                role=role,
                token_hash=cls.hash_token(raw_token),
                expires_at=now
                + timedelta(hours=settings.STAFF_INVITATION_EXPIRATION_HOURS),
            )

        invitation_url = request.build_absolute_uri(
            reverse('backoffice:staff_invitation_accept', args=[raw_token])
        )
        context = {
            'user': user,
            'invited_by': invited_by,
            'role_label': dict(StaffInvitation.Role.choices)[role],
            'invitation_url': invitation_url,
            'expiration_hours': settings.STAFF_INVITATION_EXPIRATION_HOURS,
        }
        email_options = EmailConfigurationService.email_options(fail_silently=True)
        message = EmailMultiAlternatives(
            subject='Invitación al equipo de TRUEC@',
            body=render_to_string('backoffice/emails/staff_invitation.txt', context),
            from_email=email_options['from_email'],
            to=[user.email],
            connection=email_options['connection'],
        )
        message.attach_alternative(
            render_to_string('backoffice/emails/staff_invitation.html', context),
            'text/html',
        )
        if not message.send(fail_silently=True):
            invitation.delete()
            return None
        return invitation

    @classmethod
    def pending(cls, raw_token):
        now = timezone.now()
        try:
            invitation = StaffInvitation.objects.select_related('user').get(
                token_hash=cls.hash_token(raw_token),
                accepted_at__isnull=True,
                invalidated_at__isnull=True,
                expires_at__gt=now,
            )
        except StaffInvitation.DoesNotExist:
            return None
        if not invitation.user.is_active or not invitation.user.is_staff:
            return None
        return PendingStaffInvitation(invitation, invitation.user)

    @staticmethod
    def accept(invitation):
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=['accepted_at'])
