from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class LoginTests(TestCase):
    def setUp(self):
        self.password = 'Una-clave-segura-2026'
        self.user = get_user_model().objects.create_user(
            email='persona@example.com',
            first_name='Persona',
            password=self.password,
        )

    def test_login_page_renders(self):
        response = self.client.get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Iniciar sesión')
        self.assertContains(response, 'csrfmiddlewaretoken')

    def test_valid_user_can_log_in(self):
        response = self.client.post(
            reverse('login'),
            {'username': self.user.email, 'password': self.password},
        )

        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(int(self.client.session[SESSION_KEY]), self.user.pk)

    def test_login_respects_next_destination(self):
        response = self.client.post(
            f"{reverse('login')}?next={reverse('password_reset')}",
            {
                'username': self.user.email,
                'password': self.password,
                'next': reverse('password_reset'),
            },
        )

        self.assertRedirects(response, reverse('password_reset'))

    def test_invalid_credentials_show_error_and_keep_email(self):
        response = self.client.post(
            reverse('login'),
            {'username': self.user.email, 'password': 'incorrecta'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Correo electrónico o contraseña incorrectos.')
        self.assertContains(response, self.user.email)

    def test_unknown_user_gets_generic_error(self):
        response = self.client.post(
            reverse('login'),
            {'username': 'nadie@example.com', 'password': self.password},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Correo electrónico o contraseña incorrectos.')

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        response = self.client.post(
            reverse('login'),
            {'username': self.user.email, 'password': self.password},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Correo electrónico o contraseña incorrectos.')

    def test_email_authentication_is_case_insensitive(self):
        response = self.client.post(
            reverse('login'),
            {'username': self.user.email.upper(), 'password': self.password},
        )

        self.assertRedirects(response, reverse('dashboard'))

    def test_authenticated_user_is_redirected(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('login'))

        self.assertRedirects(response, reverse('dashboard'))

    def test_external_next_url_is_not_followed(self):
        response = self.client.post(
            reverse('login'),
            {
                'username': self.user.email,
                'password': self.password,
                'next': 'https://example.net/phishing',
            },
        )

        self.assertRedirects(response, reverse('dashboard'))

    def test_staff_keeps_existing_post_login_destination(self):
        staff_user = get_user_model().objects.create_user(
            email='staff@example.com',
            first_name='Staff',
            password=self.password,
            is_staff=True,
        )

        response = self.client.post(
            reverse('login'),
            {'username': staff_user.email, 'password': self.password},
        )

        self.assertRedirects(response, reverse('profile'))

    def test_login_post_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            reverse('login'),
            {'username': self.user.email, 'password': self.password},
        )

        self.assertEqual(response.status_code, 403)

    def test_password_reset_page_is_prepared(self):
        response = self.client.get(reverse('password_reset'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Recuperar contraseña')

    def test_logout_requires_post_and_invalidates_session(self):
        self.client.force_login(self.user)

        get_response = self.client.get(reverse('logout'))
        post_response = self.client.post(reverse('logout'))

        self.assertEqual(get_response.status_code, 405)
        self.assertRedirects(post_response, reverse('home'))
        self.assertNotIn(SESSION_KEY, self.client.session)
