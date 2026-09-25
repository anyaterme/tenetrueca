from django.apps import AppConfig
from django.db.models.signals import post_migrate


def ensure_configuration_permissions(sender, **kwargs):
    from django.contrib.auth.models import Group, Permission

    from core.roles import ROLE_STAFF_ADMIN

    permission = Permission.objects.filter(
        content_type__app_label='configuration',
        content_type__model='emailconfiguration',
        codename='manage_email_configuration',
    ).first()
    if permission is not None:
        admin_group, _ = Group.objects.get_or_create(name=ROLE_STAFF_ADMIN)
        admin_group.permissions.add(permission)


class ConfigurationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'configuration'
    verbose_name = 'Configuracion funcional'

    def ready(self):
        post_migrate.connect(
            ensure_configuration_permissions,
            sender=self,
            dispatch_uid='configuration.ensure_configuration_permissions',
        )
