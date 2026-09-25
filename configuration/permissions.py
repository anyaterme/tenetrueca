from django.contrib.auth.decorators import permission_required


MANAGE_EMAIL_CONFIGURATION_PERMISSION = (
    'configuration.manage_email_configuration'
)


email_configuration_required = permission_required(
    MANAGE_EMAIL_CONFIGURATION_PERMISSION,
    raise_exception=True,
)


def user_can_manage_email_configuration(user):
    return bool(
        user.is_authenticated
        and user.is_active
        and user.has_perm(MANAGE_EMAIL_CONFIGURATION_PERMISSION)
    )
