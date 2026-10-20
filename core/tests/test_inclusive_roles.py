from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from core.permissions import user_is_administrator, user_is_manager


class InclusiveRoleMigrationTests(TestCase):
    def setUp(self):
        self.migration = import_module('core.migrations.0001_inclusive_role_names')
        self.schema_editor = SimpleNamespace(connection=connection)
        # post_migrate bootstraps some current roles; model a pre-upgrade database.
        Group.objects.filter(
            name__in=[name for pair in self.migration.ROLE_RENAMES for name in pair],
        ).delete()

    def test_rename_preserves_group_ids_memberships_permissions_and_access(self):
        User = get_user_model()
        permission = Permission.objects.get(
            content_type__app_label='accounts', codename='manage_staff',
        )
        existing_groups = []
        for index, (old, new) in enumerate(self.migration.ROLE_RENAMES):
            group = Group.objects.create(name=old)
            group.permissions.add(permission)
            user = User.objects.create_user(
                email=f'role-{index}@example.test', first_name='Persona', is_staff=True,
            )
            user.groups.add(group)
            existing_groups.append((group, user, old, new))

        self.migration.forwards(apps, self.schema_editor)
        self.migration.forwards(apps, self.schema_editor)

        for group, user, old, new in existing_groups:
            group.refresh_from_db()
            self.assertEqual(group.name, new)
            self.assertTrue(user.groups.filter(pk=group.pk, name=new).exists())
            self.assertTrue(group.permissions.filter(pk=permission.pk).exists())
            self.assertFalse(Group.objects.filter(name=old).exists())
            if old == 'Administrador':
                self.assertTrue(user_is_administrator(user))
                self.assertTrue(user.has_perm('accounts.manage_staff'))
            elif old == 'Gestor':
                self.assertTrue(user_is_manager(user))

        self.migration.backwards(apps, self.schema_editor)
        for group, user, old, new in existing_groups:
            group.refresh_from_db()
            self.assertEqual(group.name, old)
            self.assertTrue(user.groups.filter(pk=group.pk).exists())
            self.assertTrue(group.permissions.filter(pk=permission.pk).exists())

    def test_conflicting_names_do_not_merge_permissions_or_partially_rename(self):
        for old, new in self.migration.ROLE_RENAMES:
            Group.objects.create(name=old)
        conflicting_name = self.migration.ROLE_RENAMES[-1][1]
        Group.objects.create(name=conflicting_name)
        initial_count = Group.objects.count()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.migration.forwards(apps, self.schema_editor)

        for old, new in self.migration.ROLE_RENAMES:
            self.assertTrue(Group.objects.filter(name=old).exists())
        self.assertEqual(Group.objects.count(), initial_count)
