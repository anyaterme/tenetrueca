import hashlib
import ipaddress
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from accounts.models import RegistrationVerificationToken, User
from accounts.validators import normalize_nif_nie
from configuration.services import EmailConfigurationService
from core.roles import ROLE_CITIZEN
from points.services import award_registration_points


logger = logging.getLogger(__name__)


class RegistrationDeliveryError(Exception):
    pass


class InvalidRegistrationToken(Exception):
    pass


@dataclass(frozen=True)
class RegistrationStartResult:
    email: str
    email_sent: bool
    rate_limited: bool = False


@dataclass(frozen=True)
class PendingRegistration:
    record: RegistrationVerificationToken
    user: User


class RegistrationService:
    TOKEN_BYTES = 32

    @staticmethod
    def normalize_email(email):
        return User.objects.normalize_email(email).strip().lower()

    @staticmethod
    def hash_token(raw_token):
        return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

    @staticmethod
    def request_ip(request):
        candidate = request.META.get('REMOTE_ADDR', '').strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return None

    @staticmethod
    def mask_email(email):
        local_part, separator, domain = (email or '').partition('@')
        if not separator:
            return ''
        visible = local_part[:1]
        return f'{visible}{"*" * max(3, len(local_part) - 1)}@{domain}'

    @classmethod
    def start(cls, *, request, cleaned_data):
        email = cls.normalize_email(cleaned_data['email'])
        nif_nie = normalize_nif_nie(cleaned_data['nif_nie'])
        now = timezone.now()

        try:
            with transaction.atomic():
                user = (
                    User.objects.select_for_update()
                    .filter(email__iexact=email)
                    .first()
                )
                if user is not None:
                    if not cls._is_pending_public_user(user):
                        return RegistrationStartResult(email=email, email_sent=False)
                    return cls._issue_locked(request=request, user=user, now=now)

                if User.objects.filter(nif_nie__iexact=nif_nie).exists():
                    return RegistrationStartResult(email=email, email_sent=False)

                try:
                    with transaction.atomic():
                        user = User(
                            email=email,
                            username=None,
                            first_name=cleaned_data['first_name'].strip(),
                            last_name=cleaned_data['last_name'].strip(),
                            nif_nie=nif_nie,
                            is_active=False,
                            is_staff=False,
                            is_superuser=False,
                            account_status=User.AccountStatus.PENDING,
                            auth_source=User.AuthSource.LOCAL,
                            consent_version=settings.TERMS_CONSENT_VERSION,
                            consent_accepted_at=now,
                        )
                        user.set_unusable_password()
                        user.save()
                except IntegrityError:
                    return RegistrationStartResult(email=email, email_sent=False)

                return cls._issue_locked(request=request, user=user, now=now)
        except RegistrationDeliveryError:
            raise
        except Exception as error:
            logger.exception('No se pudo iniciar el registro público.')
            raise RegistrationDeliveryError from error

    @classmethod
    def resend(cls, *, request, email):
        normalized_email = cls.normalize_email(email)
        now = timezone.now()
        try:
            with transaction.atomic():
                user = (
                    User.objects.select_for_update()
                    .filter(email__iexact=normalized_email)
                    .first()
                )
                if user is None or not cls._is_pending_public_user(user):
                    return RegistrationStartResult(email=normalized_email, email_sent=False)
                return cls._issue_locked(request=request, user=user, now=now)
        except RegistrationDeliveryError:
            raise
        except Exception as error:
            logger.exception('No se pudo reenviar la verificación de registro.')
            raise RegistrationDeliveryError from error

    @classmethod
    def _issue_locked(cls, *, request, user, now):
        cooldown_start = now - timedelta(
            seconds=settings.REGISTRATION_VERIFICATION_COOLDOWN_SECONDS
        )
        if RegistrationVerificationToken.objects.filter(
            user=user,
            created_at__gte=cooldown_start,
        ).exists():
            return RegistrationStartResult(
                email=user.email,
                email_sent=False,
                rate_limited=True,
            )

        RegistrationVerificationToken.objects.filter(
            user=user,
            used_at__isnull=True,
            invalidated_at__isnull=True,
        ).update(invalidated_at=now)

        raw_token = secrets.token_urlsafe(cls.TOKEN_BYTES)
        RegistrationVerificationToken.objects.create(
            user=user,
            token_hash=cls.hash_token(raw_token),
            expires_at=now + timedelta(
                minutes=settings.REGISTRATION_VERIFICATION_EXPIRATION_MINUTES
            ),
            requested_ip=cls.request_ip(request),
        )
        cls._send_verification_email(
            request=request,
            user=user,
            raw_token=raw_token,
        )
        return RegistrationStartResult(email=user.email, email_sent=True)

    @classmethod
    def _send_verification_email(cls, *, request, user, raw_token):
        activation_url = request.build_absolute_uri(
            reverse('registration-activate', kwargs={'token': raw_token})
        )
        context = {
            'user': user,
            'activation_url': activation_url,
            'expiration_minutes': settings.REGISTRATION_VERIFICATION_EXPIRATION_MINUTES,
        }
        try:
            email_options = EmailConfigurationService.email_options(fail_silently=True)
            message = EmailMultiAlternatives(
                subject='Completa tu registro en TRUEC@',
                body=render_to_string('accounts/emails/registration_verification.txt', context),
                from_email=email_options['from_email'],
                to=[user.email],
                connection=email_options['connection'],
            )
            message.attach_alternative(
                render_to_string('accounts/emails/registration_verification.html', context),
                'text/html',
            )
            if not message.send(fail_silently=True):
                raise RegistrationDeliveryError
        except RegistrationDeliveryError:
            raise
        except Exception as error:
            logger.exception('Falló el envío de una verificación de registro.')
            raise RegistrationDeliveryError from error

    @staticmethod
    def _is_pending_public_user(user):
        return (
            not user.is_active
            and not user.is_staff
            and not user.is_superuser
            and user.account_status == User.AccountStatus.PENDING
            and user.auth_source == User.AuthSource.LOCAL
            and not user.has_usable_password()
        )

    @classmethod
    def pending(cls, raw_token):
        now = timezone.now()
        try:
            record = RegistrationVerificationToken.objects.select_related('user').get(
                token_hash=cls.hash_token(raw_token),
                used_at__isnull=True,
                invalidated_at__isnull=True,
                expires_at__gt=now,
            )
        except RegistrationVerificationToken.DoesNotExist:
            return None
        if not cls._is_pending_public_user(record.user):
            return None
        return PendingRegistration(record=record, user=record.user)

    @classmethod
    def complete(cls, *, raw_token, password):
        now = timezone.now()
        with transaction.atomic():
            try:
                record = RegistrationVerificationToken.objects.select_for_update().get(
                    token_hash=cls.hash_token(raw_token)
                )
            except RegistrationVerificationToken.DoesNotExist as error:
                raise InvalidRegistrationToken from error

            user = User.objects.select_for_update().get(pk=record.user_id)
            if (
                record.used_at is not None
                or record.invalidated_at is not None
                or record.expires_at <= now
                or not cls._is_pending_public_user(user)
            ):
                raise InvalidRegistrationToken

            validate_password(password, user=user)
            user.set_password(password)
            user.is_active = True
            user.account_status = User.AccountStatus.ACTIVE
            user.save(update_fields=['password', 'is_active', 'account_status'])

            citizen_group, _ = Group.objects.get_or_create(name=ROLE_CITIZEN)
            user.groups.add(citizen_group)
            award_registration_points(user=user)

            record.used_at = now
            record.save(update_fields=['used_at'])
            RegistrationVerificationToken.objects.filter(
                user=user,
                used_at__isnull=True,
                invalidated_at__isnull=True,
            ).exclude(pk=record.pk).update(invalidated_at=now)
            return user
