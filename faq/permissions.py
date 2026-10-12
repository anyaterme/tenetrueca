from functools import wraps

from django.core.exceptions import PermissionDenied

from core.roles import ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER


def user_can_manage_faq(user, action='view'):
    return bool(
        user.is_authenticated
        and user.is_active
        and (
            user.is_superuser
            or user.is_staff
            or user.groups.filter(name__in=(ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER)).exists()
        )
        and user.has_perm(f'faq.{action}_faq')
    )


def faq_permission_required(action):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not user_can_manage_faq(request.user, action):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator
