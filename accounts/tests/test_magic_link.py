import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import MagicLoginToken
from accounts.services import MagicLinkService
from audit.models import AuditEvent


@override_settings(
    AUTH_PROVIDER='local',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@tenetrueca.local',
    MAGIC_LOGIN_ENABLED=True,
    MAGIC_LOGIN_EXPIRATION_MINUTES=30,
    MAGIC_LOGIN_COOLDOWN_SECONDS=60,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class MagicLinkTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='persona@example.com',
            username='persona',
            first_name='Persona',
            password='Clave-2026',
            account_status=get_user_model().AccountStatus.ACTIVE,
        )

    def request_link(self, identifier='persona@example.com', next_path=''):
        data = {'identifier': identifier}
        if next_path:
            data['next'] = next_path
        return self.client.post(reverse('magic-login-request'), data)

    def raw_token_from_email(self, index=-1):
        match = re.search(r'/cuenta/acceso/([^/\s]+)/', mail.outbox[index].body)
        self.assertIsNotNone(match)
        return match.group(1)

    def age_latest_token_past_cooldown(self):
        MagicLoginToken.objects.update(
            created_at=timezone.now() - timedelta(seconds=61),
        )

    def test_existing_user_request_creates_token_and_sends_email(self):
        before = timezone.now()

        response = self.request_link()

        self.assertRedirects(response, reverse('magic-login-requested'))
        self.assertEqual(len(mail.outbox), 1)
        token = MagicLoginToken.objects.get()
        self.assertEqual(token.user, self.user)
        self.assertAlmostEqual(
            (token.expires_at - token.created_at).total_seconds(),
            30 * 60,
            delta=1,
        )
        self.assertGreaterEqual(token.created_at, before)
        self.assertEqual(token.requested_ip, '127.0.0.1')

    def test_request_accepts_username(self):
        response = self.request_link(identifier='PeRsOnA')

        self.assertRedirects(response, reverse('magic-login-requested'))
        self.assertEqual(MagicLoginToken.objects.get().user, self.user)
        self.assertEqual(len(mail.outbox), 1)

    def test_nonexistent_user_has_same_visible_response(self):
        existing_response = self.request_link()
        self.age_latest_token_past_cooldown()
        missing_response = self.request_link(identifier='nadie@example.com')

        self.assertEqual(existing_response.status_code, missing_response.status_code)
        self.assertEqual(existing_response['Location'], missing_response['Location'])
        confirmation = self.client.get(missing_response['Location'])
        self.assertContains(
            confirmation,
            'Si existe una cuenta asociada a esa dirección, recibirás un enlace para iniciar sesión.',
        )
        self.assertEqual(MagicLoginToken.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    @patch('accounts.services.magic_link.EmailMultiAlternatives.send', return_value=0)
    def test_email_delivery_failure_keeps_generic_response_and_removes_token(self, send):
        response = self.request_link()

        self.assertRedirects(response, reverse('magic-login-requested'))
        self.assertEqual(MagicLoginToken.objects.count(), 0)
        send.assert_called_once_with(fail_silently=True)

    def test_database_contains_hash_but_not_original_token(self):
        self.request_link()
        raw_token = self.raw_token_from_email()
        stored = MagicLoginToken.objects.get().token_hash

        self.assertNotEqual(stored, raw_token)
        self.assertNotIn(raw_token, stored)
        self.assertEqual(stored, MagicLinkService.hash_token(raw_token))

    def test_valid_token_logs_user_in_marks_used_and_audits(self):
        self.request_link()
        raw_token = self.raw_token_from_email()

        response = self.client.get(
            reverse('magic-login-consume', kwargs={'token': raw_token})
        )

        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)
        token = MagicLoginToken.objects.get()
        self.assertIsNotNone(token.used_at)
        event = AuditEvent.objects.get(action='magic_login.used')
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.entity_id, str(token.pk))
        self.assertNotIn(raw_token, str(event.metadata))

    def test_expired_token_does_not_log_user_in(self):
        self.request_link()
        raw_token = self.raw_token_from_email()
        MagicLoginToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        response = self.client.get(
            reverse('magic-login-consume', kwargs={'token': raw_token})
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Este enlace ya no es válido', status_code=400)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertIsNone(MagicLoginToken.objects.get().used_at)

    def test_used_token_cannot_be_reused(self):
        self.request_link()
        raw_token = self.raw_token_from_email()
        url = reverse('magic-login-consume', kwargs={'token': raw_token})

        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.logout()
        response = self.client.get(url)

        self.assertEqual(response.status_code, 400)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(AuditEvent.objects.filter(action='magic_login.used').count(), 1)

    def test_inactive_user_cannot_consume_previously_issued_token(self):
        self.request_link()
        raw_token = self.raw_token_from_email()
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        response = self.client.get(
            reverse('magic-login-consume', kwargs={'token': raw_token})
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_new_token_invalidates_previous_pending_token(self):
        self.request_link()
        first_raw_token = self.raw_token_from_email()
        first_record = MagicLoginToken.objects.get()
        self.age_latest_token_past_cooldown()

        self.request_link(identifier='persona')

        first_record.refresh_from_db()
        self.assertIsNotNone(first_record.invalidated_at)
        self.assertEqual(MagicLoginToken.objects.count(), 2)
        response = self.client.get(
            reverse('magic-login-consume', kwargs={'token': first_raw_token})
        )
        self.assertEqual(response.status_code, 400)

    def test_cooldown_suppresses_repeated_issue_without_changing_response(self):
        first_response = self.request_link()
        second_response = self.request_link(identifier='persona')

        self.assertEqual(first_response.status_code, second_response.status_code)
        self.assertEqual(first_response['Location'], second_response['Location'])
        self.assertEqual(MagicLoginToken.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_safe_internal_next_is_preserved(self):
        next_path = reverse('preferences') + '?tab=correo'
        self.request_link(next_path=next_path)
        raw_token = self.raw_token_from_email()

        response = self.client.get(
            reverse('magic-login-consume', kwargs={'token': raw_token})
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], next_path)

    def test_external_next_is_rejected(self):
        self.request_link(next_path='https://evil.example/robar-sesion')
        raw_token = self.raw_token_from_email()
        self.assertEqual(MagicLoginToken.objects.get().redirect_path, '')

        response = self.client.get(
            reverse('magic-login-consume', kwargs={'token': raw_token})
        )

        self.assertRedirects(response, reverse('dashboard'))

    @override_settings(AUTH_PROVIDER='remote')
    def test_magic_link_is_independent_from_password_provider(self):
        response = self.request_link(identifier='persona')
        raw_token = self.raw_token_from_email()

        self.assertRedirects(response, reverse('magic-login-requested'))
        consume_response = self.client.get(
            reverse('magic-login-consume', kwargs={'token': raw_token})
        )
        self.assertRedirects(consume_response, reverse('dashboard'))

    @override_settings(MAGIC_LOGIN_ENABLED=False)
    def test_disabled_feature_hides_entry_and_rejects_endpoints(self):
        login_response = self.client.get(reverse('login'))
        request_response = self.client.get(reverse('magic-login-request'))
        consume_response = self.client.get(
            reverse('magic-login-consume', kwargs={'token': 'not-a-token'})
        )

        self.assertNotContains(login_response, 'Recibir enlace para iniciar sesión')
        self.assertEqual(request_response.status_code, 403)
        self.assertEqual(consume_response.status_code, 403)
