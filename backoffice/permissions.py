from functools import wraps

from django.core.exceptions import PermissionDenied

from core.roles import ROLE_STAFF_ADMIN


MANAGE_STAFF_PERMISSION = 'accounts.manage_staff'


def user_can_manage_staff(user):
    if not user.is_authenticated or not user.is_active:
        return False
    is_administrator = user.is_superuser or user.groups.filter(
        name=ROLE_STAFF_ADMIN
    ).exists()
    return bool(
        is_administrator
        and user.has_perm(MANAGE_STAFF_PERMISSION)
    )


def staff_management_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not user_can_manage_staff(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped
