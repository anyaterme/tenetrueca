from functools import wraps

from django.core.exceptions import PermissionDenied
from core.permissions import user_is_administrator


def user_can_manage_centers(user, action='view'):
    return user_is_administrator(user) and user.has_perm(f'locations.{action}_recyclingcenter')


def center_permission_required(action):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not user_can_manage_centers(request.user, action):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator
