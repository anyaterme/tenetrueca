import base64
import hashlib
import logging
import smtplib
import socket
import ssl
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction

from audit.models import AuditEvent
from configuration.models import EmailConfiguration


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EffectiveEmailConfiguration:
    backend: str
    host: str
    port: int
    username: str
    password: str
    security: str
    default_from_email: str
    timeout: int
    sources: dict

    @property
    def use_tls(self):
        return self.security == EmailConfiguration.Security.STARTTLS

    @property
    def use_ssl(self):
        return self.security == EmailConfiguration.Security.SSL_TLS


class EmailConfigurationService:
    SINGLETON_PK = 1
    MODEL_TO_SETTING = {
        'email_host': 'EMAIL_HOST',
        'email_port': 'EMAIL_PORT',
        'email_host_user': 'EMAIL_HOST_USER',
        'security': 'EMAIL_SECURITY',
        'default_from_email': 'DEFAULT_FROM_EMAIL',
        'email_timeout': 'EMAIL_TIMEOUT',
    }
    PASSWORD_SETTING = 'EMAIL_HOST_PASSWORD'

    @classmethod
    def get_override(cls):
        return EmailConfiguration.objects.filter(pk=cls.SINGLETON_PK).first()

    @staticmethod
    def _environment_security():
        use_tls = bool(getattr(settings, 'EMAIL_USE_TLS', False))
        use_ssl = bool(getattr(settings, 'EMAIL_USE_SSL', False))
        if use_tls and use_ssl:
            raise ImproperlyConfigured(
                'EMAIL_USE_TLS y EMAIL_USE_SSL no pueden estar activos simultáneamente.'
            )
        if use_ssl:
            return EmailConfiguration.Security.SSL_TLS
        if use_tls:
            return EmailConfiguration.Security.STARTTLS
        return EmailConfiguration.Security.NONE

    @staticmethod
    def _cipher():
        raw_key = str(getattr(settings, 'EMAIL_SETTINGS_ENCRYPTION_KEY', '') or '')
        if not raw_key:
            raise ImproperlyConfigured(
                'Define EMAIL_SETTINGS_ENCRYPTION_KEY para almacenar credenciales SMTP.'
            )
        derived_key = base64.urlsafe_b64encode(
            hashlib.sha256(raw_key.encode('utf-8')).digest()
        )
        return Fernet(derived_key)

    @classmethod
    def encrypt_password(cls, value):
        return cls._cipher().encrypt(value.encode('utf-8')).decode('ascii')

    @classmethod
    def decrypt_password(cls, value):
        if value is None:
            return str(getattr(settings, 'EMAIL_HOST_PASSWORD', '') or '')
        try:
            return cls._cipher().decrypt(value.encode('ascii')).decode('utf-8')
        except (InvalidToken, ValueError, UnicodeError) as error:
            raise ImproperlyConfigured(
                'No se pudo descifrar la credencial SMTP almacenada.'
            ) from error

    @classmethod
    def effective(cls):
        override = cls.get_override()
        sources = {}

        def value(model_field, setting_name, fallback=''):
            override_value = getattr(override, model_field) if override else None
            if override_value is not None:
                sources[setting_name] = 'database'
                return override_value
            sources[setting_name] = 'environment'
            return getattr(settings, setting_name, fallback)

        encrypted_password = (
            override.email_host_password_encrypted if override else None
        )
        sources[cls.PASSWORD_SETTING] = (
            'database' if encrypted_password is not None else 'environment'
        )
        if override and override.security is not None:
            security = override.security
            sources['EMAIL_SECURITY'] = 'database'
        else:
            security = cls._environment_security()
            sources['EMAIL_SECURITY'] = 'environment'
        if security not in EmailConfiguration.Security.values:
            raise ImproperlyConfigured('La seguridad SMTP configurada no es válida.')

        return EffectiveEmailConfiguration(
            backend=str(getattr(settings, 'EMAIL_BACKEND', '')),
            host=str(value('email_host', 'EMAIL_HOST', '') or ''),
            port=int(value('email_port', 'EMAIL_PORT', 587)),
            username=str(value('email_host_user', 'EMAIL_HOST_USER', '') or ''),
            password=cls.decrypt_password(encrypted_password),
            security=security,
            default_from_email=str(
                value('default_from_email', 'DEFAULT_FROM_EMAIL', '') or ''
            ),
            timeout=int(value('email_timeout', 'EMAIL_TIMEOUT', 10)),
            sources=sources,
        )

    @classmethod
    def _connection_for(cls, effective, *, fail_silently=False):
        return get_connection(
            backend=effective.backend,
            fail_silently=fail_silently,
            host=effective.host,
            port=effective.port,
            username=effective.username,
            password=effective.password,
            use_tls=effective.use_tls,
            use_ssl=effective.use_ssl,
            timeout=effective.timeout,
        )

    @classmethod
    def get_connection(cls, *, fail_silently=False):
        return cls._connection_for(
            cls.effective(),
            fail_silently=fail_silently,
        )

    @classmethod
    def email_options(cls, *, fail_silently=False):
        effective = cls.effective()
        return {
            'from_email': effective.default_from_email,
            'connection': cls._connection_for(
                effective,
                fail_silently=fail_silently,
            ),
        }

    @staticmethod
    def _record_audit(*, actor, action, result='success', metadata=None):
        return AuditEvent.objects.create(
            actor=actor,
            action=action,
            entity='EmailConfiguration',
            entity_id='global',
            source='backoffice',
            result=result,
            metadata=metadata or {},
        )

    @classmethod
    @transaction.atomic
    def save_overrides(
        cls,
        *,
        actor,
        overrides,
        password_action,
        new_password='',
    ):
        record = (
            EmailConfiguration.objects.select_for_update()
            .filter(pk=cls.SINGLETON_PK)
            .first()
        )
        previous = {
            field: getattr(record, field) if record else None
            for field in cls.MODEL_TO_SETTING
        }
        previous_password = (
            record.email_host_password_encrypted if record else None
        )

        password_value = previous_password
        password_audit_action = None
        if password_action == 'replace':
            password_value = cls.encrypt_password(new_password)
            password_audit_action = 'configuration.email.password_changed'
        elif password_action == 'inherit':
            password_value = None
            if previous_password is not None:
                password_audit_action = 'configuration.email.password_removed'

        normalized = {
            field: overrides.get(field)
            for field in cls.MODEL_TO_SETTING
        }
        has_any_override = any(value is not None for value in normalized.values()) or (
            password_value is not None
        )
        changed_fields = [
            setting_name
            for field, setting_name in cls.MODEL_TO_SETTING.items()
            if previous[field] != normalized[field]
        ]
        if password_action == 'replace' or (
            password_action == 'inherit' and previous_password is not None
        ):
            changed_fields.append(cls.PASSWORD_SETTING)

        if has_any_override:
            if record is None:
                record = EmailConfiguration(pk=cls.SINGLETON_PK)
            for field, field_value in normalized.items():
                setattr(record, field, field_value)
            record.email_host_password_encrypted = password_value
            record.updated_by = actor
            record.save()
        elif record is not None:
            record.delete()
            record = None

        if changed_fields:
            cls._record_audit(
                actor=actor,
                action='configuration.email.updated',
                metadata={'changed_fields': sorted(set(changed_fields))},
            )
        if password_audit_action:
            cls._record_audit(
                actor=actor,
                action=password_audit_action,
                metadata={'field': cls.PASSWORD_SETTING},
            )
        return record

    @classmethod
    @transaction.atomic
    def reset(cls, *, actor):
        record = (
            EmailConfiguration.objects.select_for_update()
            .filter(pk=cls.SINGLETON_PK)
            .first()
        )
        if record is None:
            return False
        overridden_fields = [
            setting_name
            for field, setting_name in cls.MODEL_TO_SETTING.items()
            if getattr(record, field) is not None
        ]
        password_was_overridden = record.email_host_password_encrypted is not None
        if password_was_overridden:
            overridden_fields.append(cls.PASSWORD_SETTING)
        record.delete()
        cls._record_audit(
            actor=actor,
            action='configuration.email.reset',
            metadata={'removed_fields': sorted(overridden_fields)},
        )
        if password_was_overridden:
            cls._record_audit(
                actor=actor,
                action='configuration.email.password_removed',
                metadata={'field': cls.PASSWORD_SETTING},
            )
        return True

    @classmethod
    def send_test(cls, *, recipient, actor):
        effective = cls.effective()
        message = EmailMultiAlternatives(
            subject='Prueba de correo de Tenetrueca',
            body=(
                'Este mensaje confirma que la configuración de correo de '
                'Tenetrueca funciona correctamente.'
            ),
            from_email=effective.default_from_email,
            to=[recipient],
            connection=cls._connection_for(effective, fail_silently=False),
        )
        try:
            sent = message.send(fail_silently=False)
            if sent != 1:
                raise RuntimeError('El backend no confirmó el envío.')
        except Exception as error:
            logger.error(
                'Email configuration test failed (%s).',
                error.__class__.__name__,
            )
            cls._record_audit(
                actor=actor,
                action='configuration.email.test_failed',
                result='failure',
                metadata={'error_type': error.__class__.__name__},
            )
            raise
        cls._record_audit(
            actor=actor,
            action='configuration.email.test_sent',
            metadata={'recipient': recipient},
        )
        return True

    @staticmethod
    def public_error_message(error):
        if isinstance(error, smtplib.SMTPAuthenticationError):
            return 'El servidor rechazó las credenciales SMTP.'
        if isinstance(error, smtplib.SMTPRecipientsRefused):
            return 'El servidor rechazó la dirección destinataria.'
        if isinstance(error, (TimeoutError, socket.timeout)):
            return 'La conexión con el servidor SMTP superó el tiempo de espera.'
        if isinstance(error, ssl.SSLError):
            return 'No se pudo establecer una conexión TLS/SSL segura.'
        if isinstance(
            error,
            (
                ConnectionError,
                OSError,
                smtplib.SMTPConnectError,
                smtplib.SMTPServerDisconnected,
            ),
        ):
            return 'No se pudo conectar con el servidor SMTP.'
        if isinstance(error, ImproperlyConfigured):
            return 'La configuración de correo no es válida.'
        return 'No se pudo enviar el correo de prueba. Revisa la configuración SMTP.'
