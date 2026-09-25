from django.conf import settings

from core.permissions import (
    allowed_reception_centers,
    operational_scope,
    user_can_access_backoffice,
    user_can_manage_staff,
    user_can_manage_users,
    user_can_moderate,
)
from configuration.permissions import user_can_manage_email_configuration


def authentication_capabilities(request):
    return {
        'local_auth_enabled': settings.AUTH_PROVIDER == 'local',
        'magic_login_enabled': settings.MAGIC_LOGIN_ENABLED,
    }


def staff_capabilities(request):
    scope = operational_scope(request.user)
    can_moderate = user_can_moderate(request.user)
    centers = allowed_reception_centers(request.user)
    can_receive = centers.exists()
    return {
        'can_moderate_publications': can_moderate,
        'can_receive_objects': can_receive,
        'can_manage_users': user_can_manage_users(request.user),
        'can_manage_staff': user_can_manage_staff(request.user),
        'can_manage_configuration': user_can_manage_email_configuration(request.user),
        'is_operations_staff': user_can_access_backoffice(request.user),
        'staff_centers': centers,
        'is_backoffice_administrator': scope.is_administrator,
        'is_backoffice_manager': scope.is_manager,
        'operational_center': scope.center,
    }
