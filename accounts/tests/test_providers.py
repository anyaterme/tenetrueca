from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from accounts.auth_providers.remote import RemoteAuthenticationProvider
from accounts.backends import get_authentication_provider


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AuthenticationProviderTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='provider@example.com',
            first_name='Provider',
            password='Clave-provider-2026',
        )

    @override_settings(AUTH_PROVIDER='local')
    def test_local_provider_authenticates(self):
        user = authenticate(
            username=self.user.email,
            password='Clave-provider-2026',
        )

        self.assertEqual(user, self.user)

    @override_settings(AUTH_PROVIDER='desconocido')
    def test_unknown_provider_raises_controlled_configuration_error(self):
        with self.assertRaises(ImproperlyConfigured):
            get_authentication_provider()

    @override_settings(
        AUTH_PROVIDER='remote',
        AUTH_API_URL='https://identity.invalid',
        AUTH_API_TOKEN='test-token',
        AUTH_API_TIMEOUT=5,
    )
    def test_remote_provider_exists_but_does_not_authenticate(self):
        provider = RemoteAuthenticationProvider()

        self.assertIsNone(provider.authenticate(self.user.email, 'no-se-envia'))
        self.assertIsNone(authenticate(username=self.user.email, password='Clave-provider-2026'))
