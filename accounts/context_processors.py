from django.conf import settings

from core.permissions import user_can_moderate, user_can_receive


def authentication_capabilities(request):
    return {
        'local_auth_enabled': settings.AUTH_PROVIDER == 'local',
        'magic_login_enabled': settings.MAGIC_LOGIN_ENABLED,
    }


def staff_capabilities(request):
    return {
        'can_moderate_publications': user_can_moderate(request.user),
        'can_receive_objects': user_can_receive(request.user),
    }
