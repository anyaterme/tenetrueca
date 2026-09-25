from django.conf import settings

from accounts.auth_providers.base import AuthenticationProvider


class RemoteAuthenticationProvider(AuthenticationProvider):
    """Boundary for the future corporate identity API.

    No network request is performed until its contract and reconciliation rules are
    defined. Passwords received here must never be persisted or logged.
    """

    def __init__(self):
        self.api_url = settings.AUTH_API_URL
        self.api_token = settings.AUTH_API_TOKEN
        self.timeout = settings.AUTH_API_TIMEOUT

    def authenticate(self, username, password, request=None):
        return None
