from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


@override_settings(
    AUTH_PROVIDER='local',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class PasswordFlowTests(TestCase):
    def setUp(self):
        self.password = 'Clave-anterior-2026'
        self.user = get_user_model().objects.create_user(
            email='password@example.com',
            first_name='Password',
            password=self.password,
        )

    def test_password_reset_request_is_neutral_and_sends_known_user_email(self):
        known_response = self.client.post(reverse('password_reset'), {'email': self.user.email})
        unknown_response = self.client.post(reverse('password_reset'), {'email': 'nadie@example.com'})

        self.assertRedirects(known_response, reverse('password_reset_done'))
        self.assertRedirects(unknown_response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)

    def test_valid_reset_token_changes_password(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        response = self.client.get(
            reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
        )

        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            response.url,
            {
                'new_password1': 'Clave-nueva-reset-2026',
                'new_password2': 'Clave-nueva-reset-2026',
            },
        )

        self.assertRedirects(response, reverse('password_reset_complete'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Clave-nueva-reset-2026'))

    def test_password_change_keeps_session_active(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('password-change'),
            {
                'old_password': self.password,
                'new_password1': 'Clave-nueva-perfil-2026',
                'new_password2': 'Clave-nueva-perfil-2026',
            },
        )

        self.assertRedirects(response, reverse('profile'))
        self.assertIn(SESSION_KEY, self.client.session)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Clave-nueva-perfil-2026'))

    def test_password_change_requires_authentication(self):
        response = self.client.get(reverse('password-change'))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('password-change')}",
        )

    def test_wrong_current_password_is_rejected(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('password-change'),
            {
                'old_password': 'incorrecta',
                'new_password1': 'Clave-nueva-perfil-2026',
                'new_password2': 'Clave-nueva-perfil-2026',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'contraseña antigua es incorrecta')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_django_password_validators_are_applied(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('password-change'),
            {
                'old_password': self.password,
                'new_password1': '123',
                'new_password2': '123',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors['new_password2'])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    @override_settings(AUTH_PROVIDER='remote')
    def test_remote_provider_shows_managed_message_and_rejects_post(self):
        self.client.force_login(self.user)

        get_response = self.client.get(reverse('password-change'))
        post_response = self.client.post(
            reverse('password-change'),
            {
                'old_password': self.password,
                'new_password1': 'Clave-nueva-perfil-2026',
                'new_password2': 'Clave-nueva-perfil-2026',
            },
        )

        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, 'Contraseña gestionada externamente')
        self.assertNotContains(get_response, 'name="old_password"')
        self.assertEqual(post_response.status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_external_user_cannot_change_local_password(self):
        self.user.auth_source = self.user.AuthSource.EXTERNAL
        self.user.external_auth_id = 'external-password-user'
        self.user.save(update_fields=['auth_source', 'external_auth_id'])
        self.client.force_login(self.user)

        get_response = self.client.get(reverse('password-change'))
        post_response = self.client.post(
            reverse('password-change'),
            {
                'old_password': self.password,
                'new_password1': 'Clave-nueva-perfil-2026',
                'new_password2': 'Clave-nueva-perfil-2026',
            },
        )

        self.assertContains(get_response, 'Contraseña gestionada externamente')
        self.assertEqual(post_response.status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))
