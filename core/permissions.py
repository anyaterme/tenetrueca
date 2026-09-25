from accounts.models import UserCenterAccess
from core.roles import ROLE_MODERATOR
from locations.models import RecyclingCenter


def user_can_moderate(user):
    if not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or user.groups.filter(name=ROLE_MODERATOR).exists()


def allowed_reception_centers(user):
    centers = RecyclingCenter.objects.filter(is_active=True)
    if not user.is_authenticated or not user.is_active:
        return centers.none()
    if user.is_superuser:
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
