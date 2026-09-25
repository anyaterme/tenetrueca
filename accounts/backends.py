from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ImproperlyConfigured

from accounts.auth_providers import LocalAuthenticationProvider, RemoteAuthenticationProvider


PROVIDERS = {
    'local': LocalAuthenticationProvider,
    'remote': RemoteAuthenticationProvider,
}


def get_authentication_provider():
    provider_name = settings.AUTH_PROVIDER.strip().lower()
    try:
        provider_class = PROVIDERS[provider_name]
    except KeyError as exc:
        raise ImproperlyConfigured(f'Proveedor de autenticación desconocido: {provider_name}') from exc
    return provider_class()


class TenetruecaAuthenticationBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        email = username or kwargs.get('email')
        if not email or password is None:
            return None
        return get_authentication_provider().authenticate(email, password, request=request)

    def get_user(self, user_id):
        User = get_user_model()
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
        return user if user.is_active else None
