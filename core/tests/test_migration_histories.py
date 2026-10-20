from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase


class InclusiveMigrationHistoryTests(SimpleTestCase):
    """Load the shipped migrations with each environment's legacy file inventory."""

    def load_migration(self, app, name, installed):
        with TemporaryDirectory() as folder:
            path = Path(folder) / (name + '.py')
            source = Path(__file__).resolve().parents[2] / app / 'migrations' / path.name
            path.write_text(source.read_text())
            for baseline in installed:
                (path.parent / (baseline + '.py')).touch()
            spec = spec_from_file_location('inclusive_migration_fixture', path)
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.Migration

    def test_accounts_supports_the_production_combined_history(self):
        baseline = '0005_registrationverificationtoken_alter_user_nif_nie_and_more'
        migration = self.load_migration('accounts', '0007_inclusive_labels', [baseline])
        self.assertEqual(migration.dependencies, [('accounts', baseline)])

    def test_accounts_preserves_the_development_history(self):
        baseline = '0006_registrationverificationtoken_and_nif_constraint'
        migration = self.load_migration('accounts', '0007_inclusive_labels', [baseline])
        self.assertEqual(migration.dependencies, [('accounts', baseline)])

    def test_accounts_rejects_missing_or_mixed_histories(self):
        for installed in ([], [
            '0006_registrationverificationtoken_and_nif_constraint',
            '0005_registrationverificationtoken_alter_user_nif_nie_and_more',
        ]):
            with self.subTest(installed=installed):
                with self.assertRaisesMessage(RuntimeError, 'historial base de accounts'):
                    self.load_migration('accounts', '0007_inclusive_labels', installed)

    def test_backoffice_production_does_not_replay_development_staff_assignment(self):
        migration = self.load_migration(
            'backoffice', '0004_inclusive_role_labels', ['0001_initial'],
        )
        self.assertEqual(migration.dependencies, [('backoffice', '0001_initial')])
        self.assertFalse(any(operation.__class__.__name__ == 'RunPython'
                             for operation in migration.operations))

    def test_backoffice_preserves_the_complete_development_history(self):
        migration = self.load_migration('backoffice', '0004_inclusive_role_labels', [
            '0001_initial', '0002_assign_existing_staff_roles',
            '0003_alter_staffauditevent_action',
        ])
        self.assertEqual(migration.dependencies, [
            ('backoffice', '0003_alter_staffauditevent_action'),
        ])

    def test_backoffice_requires_an_existing_baseline(self):
        with self.assertRaisesMessage(RuntimeError, 'historial base de backoffice'):
            self.load_migration('backoffice', '0004_inclusive_role_labels', [])
