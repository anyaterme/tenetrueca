from django.conf import settings

from core.permissions import user_can_moderate, user_can_receive


def authentication_capabilities(request):
    return {
        'local_auth_enabled': settings.AUTH_PROVIDER == 'local',
        'magic_login_enabled': settings.MAGIC_LOGIN_ENABLED,
    }


def staff_capabilities(request):
    can_moderate = user_can_moderate(request.user)
    can_receive = user_can_receive(request.user)
    return {
        'can_moderate_publications': can_moderate,
        'can_receive_objects': can_receive,
        'is_operations_staff': bool(
            request.user.is_authenticated
            and (request.user.is_staff or can_moderate or can_receive)
        ),
    }
