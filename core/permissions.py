from dataclasses import dataclass

from django.db.models import Q

from accounts.models import UserCenterAccess
from backoffice.permissions import user_can_manage_staff as has_staff_management_permission
from core.roles import (
    ROLE_FUNCTIONAL_ADMIN,
    ROLE_MODERATOR,
    ROLE_STAFF_ADMIN,
    ROLE_STAFF_MANAGER,
    ROLE_TECH_ADMIN,
)
from locations.models import RecyclingCenter


def user_is_administrator(user):
    if not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or user.groups.filter(name=ROLE_STAFF_ADMIN).exists()


def user_is_manager(user):
    if not user.is_authenticated or not user.is_active or user_is_administrator(user):
        return False
    return user.groups.filter(name=ROLE_STAFF_MANAGER).exists()


def manager_operational_center(user):
    """Return the manager's only active center, or None for an invalid/empty scope."""
    if not user_is_manager(user):
        return None
    centers = list(
        RecyclingCenter.objects.filter(
            is_active=True,
            staff_accesses__user=user,
            staff_accesses__is_active=True,
        )
        .distinct()
        .order_by('pk')[:2]
    )
    return centers[0] if len(centers) == 1 else None


@dataclass(frozen=True)
class OperationalScope:
    is_administrator: bool
    is_manager: bool
    center: RecyclingCenter | None

    @property
    def has_operational_scope(self):
        return self.is_administrator or (self.is_manager and self.center is not None)


def operational_scope(user):
    is_administrator = user_is_administrator(user)
    is_manager = user_is_manager(user)
    return OperationalScope(
        is_administrator=is_administrator,
        is_manager=is_manager,
        center=manager_operational_center(user) if is_manager else None,
    )


def scope_queryset(user, queryset, *, center_lookup):
    """Apply the Administrador/Gestor operational boundary to a queryset."""
    scope = operational_scope(user)
    if scope.is_administrator:
        return queryset
    if scope.is_manager:
        if scope.center is None:
            return queryset.none()
        return queryset.filter(**{center_lookup: scope.center})
    return queryset


def scope_publications(user, queryset):
    """Scope publications using their operational center before/after reception."""
    scope = operational_scope(user)
    if scope.is_administrator:
        return queryset
    if scope.is_manager:
        if scope.center is None:
            return queryset.none()
        return queryset.filter(
            Q(inventory_object__center=scope.center)
            | Q(
                inventory_object__isnull=True,
                submitter__habitual_recycling_center=scope.center,
            )
        )
    return queryset


def scope_reservations(user, queryset):
    return scope_queryset(
        user,
        queryset,
        center_lookup='inventory_item__center',
    )


def user_can_moderate(user):
    if not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or user.groups.filter(
        name__in=(ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER, ROLE_MODERATOR)
    ).exists()


def allowed_reception_centers(user):
    centers = RecyclingCenter.objects.filter(is_active=True)
    if not user.is_authenticated or not user.is_active:
        return centers.none()
    if user_is_administrator(user):
        return centers
    if user_is_manager(user):
        center = manager_operational_center(user)
        return centers.filter(pk=center.pk) if center is not None else centers.none()
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
        and (
            user.is_staff
            or user_is_manager(user)
            or user_can_moderate(user)
            or user_can_receive(user)
        )
    )


def _user_has_admin_role(user):
    if not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or user.groups.filter(
        name__in=(ROLE_STAFF_ADMIN, ROLE_FUNCTIONAL_ADMIN, ROLE_TECH_ADMIN)
    ).exists()


def user_can_manage_users(user):
    if user_is_manager(user):
        return False
    return _user_has_admin_role(user) or user.has_perm('accounts.view_user')


def user_can_manage_staff(user):
    if user_is_manager(user):
        return False
    return has_staff_management_permission(user)
