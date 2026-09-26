import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY, get_user_model
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import RegistrationVerificationToken
from accounts.services.turnstile import TurnstileVerification
from core.roles import ROLE_CITIZEN
from points.models import PointMovement
from points.services import points_balance


@override_settings(
    AUTH_PROVIDER='local',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@tenetrueca.local',
    REGISTRATION_VERIFICATION_EXPIRATION_MINUTES=60,
    REGISTRATION_VERIFICATION_COOLDOWN_SECONDS=60,
    TURNSTILE_SITE_KEY='test-site-key',
    TURNSTILE_SECRET_KEY='test-secret-key',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class RegistrationTests(TestCase):
    password = 'Clave-registro-2026'

    def setUp(self):
        self.turnstile_patcher = patch(
            'accounts.views.verify_turnstile',
            return_value=TurnstileVerification(True),
        )
        self.verify_turnstile = self.turnstile_patcher.start()
        self.addCleanup(self.turnstile_patcher.stop)

    def registration_data(self, **overrides):
        data = {
            'first_name': 'Laura',
            'last_name': 'García',
            'email': 'laura@example.com',
            'nif_nie': '12345678Z',
            'accept_terms': 'on',
            'cf-turnstile-response': 'turnstile-token',
        }
        data.update(overrides)
        return data

    def register(self, **overrides):
        return self.client.post(reverse('register'), self.registration_data(**overrides))

    def raw_token_from_email(self, index=-1):
        match = re.search(r'/cuenta/registro/activar/([^/\s]+)/', mail.outbox[index].body)
        self.assertIsNotNone(match)
        return match.group(1)

    def create_pending_registration(self):
        response = self.register()
        self.assertRedirects(response, reverse('registration-check-email'))
        return get_user_model().objects.get(email='laura@example.com')

    def test_initial_form_has_no_username_or_password_fields(self):
        response = self.client.get(reverse('register'))

        self.assertEqual(
            list(response.context['form'].fields),
            ['first_name', 'last_name', 'email', 'nif_nie', 'accept_terms'],
        )
        self.assertNotContains(response, 'name="username"')
        self.assertNotContains(response, 'name="password1"')
        self.assertNotContains(response, 'Contraseña (confirmación)')

    def test_registration_page_renders_turnstile_without_secret(self):
        response = self.client.get(reverse('register'))

        self.assertContains(response, 'class="cf-turnstile"')
        self.assertContains(response, 'test-site-key')
        self.assertNotContains(response, 'test-secret-key')

    def test_valid_registration_creates_inactive_passwordless_user_and_sends_email(self):
        response = self.register()

        self.assertRedirects(response, reverse('registration-check-email'))
        user = get_user_model().objects.get(email='laura@example.com')
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.account_status, user.AccountStatus.PENDING)
        self.assertEqual(user.auth_source, user.AuthSource.LOCAL)
        self.assertIsNone(user.username)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertIsNotNone(user.consent_accepted_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(RegistrationVerificationToken.objects.count(), 1)
        self.verify_turnstile.assert_called_once_with('turnstile-token', remote_ip='127.0.0.1')

    def test_email_and_nif_are_normalized(self):
        self.register(email='  LAURA@EXAMPLE.COM  ', nif_nie='  12345678-z ')

        user = get_user_model().objects.get()
        self.assertEqual(user.email, 'laura@example.com')
        self.assertEqual(user.nif_nie, '12345678Z')

    def test_invalid_nif_is_rejected(self):
        response = self.register(nif_nie='12345678A')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Introduce un DNI o NIE válido.')
        self.assertFalse(get_user_model().objects.exists())
        self.verify_turnstile.assert_not_called()

    def test_duplicate_email_does_not_create_second_user_or_reveal_account(self):
        get_user_model().objects.create_user(
            email='laura@example.com',
            first_name='Existente',
            password='Clave-existente-2026',
            account_status=get_user_model().AccountStatus.ACTIVE,
        )

        response = self.register(email='LAURA@example.com')

        self.assertRedirects(response, reverse('registration-check-email'))
        self.assertEqual(get_user_model().objects.filter(email__iexact='laura@example.com').count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_duplicate_nif_does_not_create_second_user(self):
        get_user_model().objects.create_user(
            email='existente@example.com',
            first_name='Existente',
            nif_nie='12345678Z',
            password='Clave-existente-2026',
            account_status=get_user_model().AccountStatus.ACTIVE,
        )

        response = self.register(email='otra@example.com')

        self.assertRedirects(response, reverse('registration-check-email'))
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_consent_is_required(self):
        data = self.registration_data()
        data.pop('accept_terms')

        response = self.client.post(reverse('register'), data)

        self.assertContains(response, 'Debes aceptar la política de privacidad')
        self.assertFalse(get_user_model().objects.exists())
        self.verify_turnstile.assert_not_called()

    def test_missing_turnstile_prevents_registration(self):
        self.verify_turnstile.return_value = TurnstileVerification(False, 'missing')
        data = self.registration_data()
        data.pop('cf-turnstile-response')

        response = self.client.post(reverse('register'), data)

        self.assertContains(response, 'Completa la verificación de seguridad')
        self.assertFalse(get_user_model().objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_turnstile_prevents_registration(self):
        self.verify_turnstile.return_value = TurnstileVerification(False, 'invalid')

        response = self.register()

        self.assertContains(response, 'Completa la verificación de seguridad')
        self.assertFalse(get_user_model().objects.exists())

    def test_turnstile_connection_error_fails_closed(self):
        self.verify_turnstile.return_value = TurnstileVerification(False, 'unavailable')

        response = self.register()

        self.assertContains(response, 'No pudimos verificar que eres una persona')
        self.assertFalse(get_user_model().objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    @patch('accounts.services.registration.EmailMultiAlternatives.send', return_value=0)
    def test_email_delivery_failure_rolls_back_new_account(self, send):
        response = self.register()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No pudimos enviar el correo de verificación')
        self.assertFalse(get_user_model().objects.exists())
        self.assertFalse(RegistrationVerificationToken.objects.exists())
        send.assert_called_once_with(fail_silently=True)

    def test_check_email_page_masks_address(self):
        self.create_pending_registration()

        response = self.client.get(reverse('registration-check-email'))

        self.assertContains(response, 'l****@example.com')
        self.assertNotContains(response, 'laura@example.com')
        self.assertContains(response, 'Reenviar correo')

    def test_token_is_hashed_and_valid_get_only_shows_password_form(self):
        user = self.create_pending_registration()
        raw_token = self.raw_token_from_email()

        response = self.client.get(reverse('registration-activate', args=[raw_token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Crear tu contraseña')
        self.assertNotEqual(RegistrationVerificationToken.objects.get().token_hash, raw_token)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())

    def test_invalid_token_is_rejected(self):
        response = self.client.get(reverse('registration-activate', args=['not-a-token']))

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Este enlace ya no es válido', status_code=400)

    def test_expired_token_is_rejected(self):
        user = self.create_pending_registration()
        raw_token = self.raw_token_from_email()
        RegistrationVerificationToken.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        response = self.client.get(reverse('registration-activate', args=[raw_token]))

        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_mismatched_passwords_do_not_activate_account(self):
        user = self.create_pending_registration()
        raw_token = self.raw_token_from_email()

        response = self.client.post(
            reverse('registration-activate', args=[raw_token]),
            {'new_password1': self.password, 'new_password2': 'Otra-clave-2026'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Los dos campos de contraseña no coinciden')
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNone(RegistrationVerificationToken.objects.get().used_at)

    def test_weak_password_does_not_activate_account(self):
        user = self.create_pending_registration()
        raw_token = self.raw_token_from_email()

        response = self.client.post(
            reverse('registration-activate', args=[raw_token]),
            {'new_password1': '123', 'new_password2': '123'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors['new_password2'])
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_valid_password_atomically_activates_logs_in_and_awards_role_and_points(self):
        user = self.create_pending_registration()
        raw_token = self.raw_token_from_email()

        response = self.client.post(
            reverse('registration-activate', args=[raw_token]),
            {'new_password1': self.password, 'new_password2': self.password},
        )

        self.assertRedirects(response, reverse('dashboard'))
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(user.account_status, user.AccountStatus.ACTIVE)
        self.assertTrue(user.check_password(self.password))
        self.assertIn(ROLE_CITIZEN, user.groups.values_list('name', flat=True))
        self.assertEqual(int(self.client.session[SESSION_KEY]), user.pk)
        self.assertEqual(points_balance(user), 100)
        self.assertEqual(
            PointMovement.objects.filter(user=user, reason=PointMovement.Reason.REGISTRATION).count(),
            1,
        )
        self.assertIsNotNone(RegistrationVerificationToken.objects.get().used_at)

    def test_consumed_token_cannot_be_reused(self):
        user = self.create_pending_registration()
        raw_token = self.raw_token_from_email()
        url = reverse('registration-activate', args=[raw_token])
        self.client.post(
            url,
            {'new_password1': self.password, 'new_password2': self.password},
        )
        self.client.logout()

        response = self.client.get(url)

        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertTrue(user.check_password(self.password))

    def test_resend_issues_new_token_and_invalidates_previous_one(self):
        self.create_pending_registration()
        first_token = RegistrationVerificationToken.objects.get()
        RegistrationVerificationToken.objects.update(
            created_at=timezone.now() - timedelta(seconds=61)
        )

        response = self.client.post(reverse('registration-resend'))

        self.assertRedirects(response, reverse('registration-check-email'))
        first_token.refresh_from_db()
        self.assertIsNotNone(first_token.invalidated_at)
        self.assertEqual(RegistrationVerificationToken.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 2)

    def test_resend_is_rate_limited(self):
        self.create_pending_registration()

        response = self.client.post(reverse('registration-resend'))

        self.assertRedirects(response, reverse('registration-check-email'))
        self.assertEqual(RegistrationVerificationToken.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_registration_post_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(reverse('register'), self.registration_data())

        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.exists())

    @override_settings(AUTH_PROVIDER='remote')
    def test_registration_is_disabled_for_remote_provider(self):
        response = self.client.get(reverse('register'))

        self.assertEqual(response.status_code, 403)
