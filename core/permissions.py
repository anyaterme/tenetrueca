from accounts.models import UserCenterAccess
from backoffice.permissions import user_can_manage_staff as has_staff_management_permission
from core.roles import (
    ROLE_FUNCTIONAL_ADMIN,
    ROLE_MODERATOR,
    ROLE_STAFF_ADMIN,
    ROLE_TECH_ADMIN,
)
from locations.models import RecyclingCenter


def user_can_moderate(user):
    if not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or user.groups.filter(
        name__in=(ROLE_STAFF_ADMIN, ROLE_MODERATOR)
    ).exists()


def allowed_reception_centers(user):
    centers = RecyclingCenter.objects.filter(is_active=True)
    if not user.is_authenticated or not user.is_active:
        return centers.none()
    if user.is_superuser or user.groups.filter(name=ROLE_STAFF_ADMIN).exists():
        return centers
    return centers.filter(
        staff_accesses__user=user,
        staff_accesses__is_active=True,
        staff_accesses__role__in=(
            UserCenterAccess.ScopeRole.OPERATOR,
            UserCenterAccess.ScopeRole.SUPERVISOR,
        ),
    ).distinct()


def user_can_receive(user):
    return allowed_reception_centers(user).exists()


def user_can_access_backoffice(user):
    return bool(
        user.is_authenticated
        and user.is_active
        and (user.is_staff or user_can_moderate(user) or user_can_receive(user))
    )


def _user_has_admin_role(user):
    if not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or user.groups.filter(
        name__in=(ROLE_STAFF_ADMIN, ROLE_FUNCTIONAL_ADMIN, ROLE_TECH_ADMIN)
    ).exists()


def user_can_manage_users(user):
    return _user_has_admin_role(user) or user.has_perm('accounts.view_user')


def user_can_manage_staff(user):
    return has_staff_management_permission(user)
