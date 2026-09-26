from django.apps import AppConfig
from django.db.models.signals import post_migrate


def ensure_staff_roles(sender, **kwargs):
    from django.contrib.auth.models import Group, Permission

    from core.roles import ROLE_CITIZEN, ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER

    Group.objects.get_or_create(name=ROLE_CITIZEN)
    admin_group, _ = Group.objects.get_or_create(name=ROLE_STAFF_ADMIN)
    Group.objects.get_or_create(name=ROLE_STAFF_MANAGER)
    permission = Permission.objects.filter(
        content_type__app_label='accounts',
        content_type__model='user',
        codename='manage_staff',
    ).first()
    if permission is not None:
        admin_group.permissions.add(permission)


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'
    verbose_name = 'Cuentas'

    def ready(self):
        post_migrate.connect(
            ensure_staff_roles,
            sender=self,
            dispatch_uid='accounts.ensure_staff_roles',
        )
