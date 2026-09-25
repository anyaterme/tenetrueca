from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from points.models import PointMovement
from points.services import points_balance


@override_settings(
    AUTH_PROVIDER='local',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class RegistrationTests(TestCase):
    def registration_data(self, **overrides):
        data = {
            'first_name': 'Laura',
            'last_name': 'García',
            'username': 'laura',
            'email': 'laura@example.com',
            'nif_nie': '12345678Z',
            'password1': 'Clave-registro-2026',
            'password2': 'Clave-registro-2026',
            'accept_terms': 'on',
        }
        data.update(overrides)
        return data

    def test_registration_creates_local_user_and_logs_in(self):
        response = self.client.post(reverse('register'), self.registration_data())

        self.assertRedirects(response, reverse('dashboard'))
        user = get_user_model().objects.get(email='laura@example.com')
        self.assertTrue(user.check_password('Clave-registro-2026'))
        self.assertEqual(user.username, 'laura')
        self.assertEqual(user.auth_source, user.AuthSource.LOCAL)
        self.assertIsNotNone(user.consent_accepted_at)
        self.assertEqual(points_balance(user), 100)
        self.assertEqual(
            PointMovement.objects.filter(
                user=user,
                reason=PointMovement.Reason.REGISTRATION,
            ).count(),
            1,
        )

    def test_duplicate_email_is_case_insensitive(self):
        get_user_model().objects.create_user(
            email='laura@example.com',
            first_name='Otra',
            password='Clave-duplicada-2026',
        )

        response = self.client.post(
            reverse('register'),
            self.registration_data(email='LAURA@example.com'),
        )

        self.assertContains(response, 'Ya existe una cuenta con este correo electrónico.')
        self.assertEqual(get_user_model().objects.filter(email__iexact='laura@example.com').count(), 1)

    def test_passwords_must_match(self):
        response = self.client.post(
            reverse('register'),
            self.registration_data(password2='Otra-clave-2026'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Los dos campos de contraseña no coinciden')

    def test_duplicate_username_is_case_insensitive(self):
        get_user_model().objects.create_user(
            email='otra@example.com',
            username='laura',
            first_name='Otra',
            password='Clave-duplicada-2026',
        )

        response = self.client.post(
            reverse('register'),
            self.registration_data(username='LAURA'),
        )

        self.assertContains(response, 'Ya existe una cuenta con este nombre de usuario.')
        self.assertFalse(get_user_model().objects.filter(email='laura@example.com').exists())

    def test_password_validators_are_applied(self):
        response = self.client.post(
            reverse('register'),
            self.registration_data(password1='123', password2='123'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(email='laura@example.com').exists())
        self.assertTrue(response.context['form'].errors['password2'])

    def test_consent_is_required(self):
        data = self.registration_data()
        data.pop('accept_terms')

        response = self.client.post(reverse('register'), data)

        self.assertContains(response, 'Debes aceptar la política de privacidad')
        self.assertFalse(get_user_model().objects.filter(email='laura@example.com').exists())

    @override_settings(AUTH_PROVIDER='remote')
    def test_registration_is_disabled_for_remote_provider(self):
        response = self.client.get(reverse('register'))

        self.assertEqual(response.status_code, 403)
