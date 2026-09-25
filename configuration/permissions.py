from functools import wraps

from django.core.exceptions import PermissionDenied


MANAGE_EMAIL_CONFIGURATION_PERMISSION = (
    'configuration.manage_email_configuration'
)


def user_can_manage_email_configuration(user):
    from core.permissions import user_is_manager

    return bool(
        user.is_authenticated
        and user.is_active
        and not user_is_manager(user)
        and user.has_perm(MANAGE_EMAIL_CONFIGURATION_PERMISSION)
    )


def email_configuration_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not user_can_manage_email_configuration(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped
