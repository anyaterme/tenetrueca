from django.apps import AppConfig
from django.db.models.signals import post_migrate


def ensure_faq_permissions(sender, using, **kwargs):
    from django.contrib.auth.models import Group, Permission
    from core.roles import ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER

    permissions = list(Permission.objects.using(using).filter(
        content_type__app_label='faq',
        content_type__model='faq',
        codename__in=('view_faq', 'add_faq', 'change_faq', 'delete_faq'),
    ))
    if permissions:
        for name in (ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER):
            group, _ = Group.objects.using(using).get_or_create(name=name)
            group.permissions.add(*permissions)


class FaqConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'faq'
    verbose_name = 'Preguntas frecuentes'

    def ready(self):
        post_migrate.connect(
            ensure_faq_permissions,
            sender=self,
            dispatch_uid='faq.ensure_faq_permissions',
        )
