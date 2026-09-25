import hashlib
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import MagicLoginToken, User


@dataclass(frozen=True)
class ConsumedMagicLink:
    record: MagicLoginToken
    user: User
    redirect_path: str


class MagicLinkService:
    TOKEN_BYTES = 32

    @staticmethod
    def hash_token(raw_token):
        return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

    @staticmethod
    def safe_redirect_path(request, candidate):
        candidate = (candidate or '').strip()
        if not candidate or not url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return ''

        parsed = urlsplit(candidate)
        path = parsed.path or '/'
        if not path.startswith('/') or path.startswith('//'):
            return ''
        return urlunsplit(('', '', path, parsed.query, ''))

    @staticmethod
    def request_ip(request):
        candidate = request.META.get('REMOTE_ADDR', '').strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return None

    @classmethod
    def request_link(cls, *, request, identifier, redirect_path=''):
        now = timezone.now()
        normalized_identifier = identifier.strip()
        lookup = (
            Q(email__iexact=normalized_identifier)
            if '@' in normalized_identifier
            else Q(username__iexact=normalized_identifier)
        )

        with transaction.atomic():
            users = list(
                User.objects.select_for_update()
                .filter(lookup, is_active=True, account_status=User.AccountStatus.ACTIVE)[:2]
            )
            if len(users) != 1:
                return None

            user = users[0]
            cooldown_start = now - timedelta(seconds=settings.MAGIC_LOGIN_COOLDOWN_SECONDS)
            if MagicLoginToken.objects.filter(
                user=user,
                created_at__gte=cooldown_start,
            ).exists():
                return None

            MagicLoginToken.objects.filter(
                user=user,
                used_at__isnull=True,
                invalidated_at__isnull=True,
            ).update(invalidated_at=now)

            raw_token = secrets.token_urlsafe(cls.TOKEN_BYTES)
            record = MagicLoginToken.objects.create(
                user=user,
                token_hash=cls.hash_token(raw_token),
                expires_at=now + timedelta(minutes=settings.MAGIC_LOGIN_EXPIRATION_MINUTES),
                requested_ip=cls.request_ip(request),
                redirect_path=cls.safe_redirect_path(request, redirect_path),
            )

        magic_url = request.build_absolute_uri(
            reverse('magic-login-consume', kwargs={'token': raw_token})
        )
        context = {
            'user': user,
            'magic_url': magic_url,
            'expiration_minutes': settings.MAGIC_LOGIN_EXPIRATION_MINUTES,
        }
        message = EmailMultiAlternatives(
            subject='Tu enlace para iniciar sesión en TRUEC@',
            body=render_to_string('accounts/emails/magic_login.txt', context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        message.attach_alternative(
            render_to_string('accounts/emails/magic_login.html', context),
            'text/html',
        )
        if not message.send(fail_silently=True):
            MagicLoginToken.objects.filter(pk=record.pk).delete()
            return None
        return record

    @classmethod
    def consume(cls, raw_token):
        token_hash = cls.hash_token(raw_token)
        now = timezone.now()

        with transaction.atomic():
            try:
                record = (
                    MagicLoginToken.objects.select_for_update()
                    .select_related('user')
                    .get(token_hash=token_hash)
                )
            except MagicLoginToken.DoesNotExist:
                return None

            user = record.user
            if (
                record.used_at is not None
                or record.invalidated_at is not None
                or record.expires_at <= now
                or not user.is_active
                or user.account_status != User.AccountStatus.ACTIVE
            ):
                return None

            record.used_at = now
            record.save(update_fields=['used_at'])
            return ConsumedMagicLink(record, user, record.redirect_path)
