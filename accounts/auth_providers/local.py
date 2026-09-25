from django.contrib.auth import get_user_model

from accounts.auth_providers.base import AuthenticationProvider


class LocalAuthenticationProvider(AuthenticationProvider):
    def authenticate(self, username, password, request=None):
        User = get_user_model()

        try:
            user = User.objects.get(email__iexact=username.strip())
        except (User.DoesNotExist, User.MultipleObjectsReturned):
            # Equalize password-hashing work without revealing whether the email exists.
            User().set_password(password)
            return None

        if not user.is_active or not user.check_password(password):
            return None

        return user
