from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


@skipIf(settings.MIGRATION_MODULES.get('assistant', 'enabled') is None,
        'Requiere artefactos de migraciones base del proyecto; usar test_postgres_settings.')
class AssistedReviewMigrationTests(TransactionTestCase):
    def test_upgrade_preserves_prior_publication_and_moderation_decision(self):
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        baseline = [target for target in targets if target[0] not in ('assistant', 'moderation')]
        baseline += [('assistant', None), ('moderation', '0001_initial')]
        try:
            executor.migrate(baseline)
            old_state = executor.loader.project_state(
                [target for target in baseline if target[1] is not None],
            ).apps
            User = old_state.get_model('accounts', 'User')
            Category = old_state.get_model('catalog', 'Category')
            Publication = old_state.get_model('publications', 'Publication')
            Decision = old_state.get_model('moderation', 'ModerationDecision')
            user = User.objects.create(username='migration-citizen', email='migration@example.test')
            category = Category.objects.create(name='Hogar', slug='migration-hogar')
            publication = Publication.objects.create(submitter=user, category=category, title='Juguete',
                                                     description='Objeto anterior a la integración.',
                                                     status='approved', submitted_at=timezone.now())
            decision = Decision.objects.create(publication=publication, reviewer=user, decision='approve',
                                               previous_status='pending_review', resulting_status='approved')
            decision_id, publication_id = decision.pk, publication.pk
        finally:
            MigrationExecutor(connection).migrate(targets)
        from moderation.models import ModerationDecision
        from publications.models import Publication

        preserved = ModerationDecision.objects.get(pk=decision_id)
        self.assertIsNone(preserved.ai_evaluation_id)
        self.assertEqual(preserved.publication_fingerprint, '')
        self.assertEqual(preserved.resulting_status, 'approved')
        self.assertEqual(Publication.objects.get(pk=publication_id).status, 'approved')
