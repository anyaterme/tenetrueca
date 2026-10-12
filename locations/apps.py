from django.apps import AppConfig
from django.db.models.signals import post_migrate


def ensure_center_permissions(sender, using, **kwargs):
    from django.contrib.auth.models import Group, Permission
    from core.roles import ROLE_STAFF_ADMIN
    permissions = list(Permission.objects.using(using).filter(
        content_type__app_label='locations', content_type__model='recyclingcenter',
        codename__in=('view_recyclingcenter', 'add_recyclingcenter', 'change_recyclingcenter'),
    ))
    if permissions:
        group, _ = Group.objects.using(using).get_or_create(name=ROLE_STAFF_ADMIN)
        group.permissions.add(*permissions)


class LocationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'locations'
    verbose_name = 'Puntos limpios'

    def ready(self):
        post_migrate.connect(ensure_center_permissions, sender=self, dispatch_uid='locations.ensure_center_permissions')
