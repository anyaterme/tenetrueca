from django.contrib.auth.decorators import permission_required


MANAGE_STAFF_PERMISSION = 'accounts.manage_staff'


staff_management_required = permission_required(
    MANAGE_STAFF_PERMISSION,
    raise_exception=True,
)


def user_can_manage_staff(user):
    return bool(
        user.is_authenticated
        and user.is_active
        and user.has_perm(MANAGE_STAFF_PERMISSION)
    )

