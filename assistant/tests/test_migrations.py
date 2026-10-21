from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


@skipIf(settings.MIGRATION_MODULES.get('assistant', 'enabled') is None,
        'Requiere artefactos de migraciones base del proyecto; usar test_postgres_settings.')
class AssistedReviewMigrationTests(TransactionTestCase):
    def test_preanalysis_upgrade_preserves_previous_evaluation_and_usage(self):
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        baseline = [target for target in targets if target[0] != 'assistant']
        baseline.append(('assistant', '0002_evaluation_error_details'))
        try:
            executor.migrate(baseline)
            apps = executor.loader.project_state(baseline).apps
            user = apps.get_model('accounts', 'User').objects.create(username='old-evaluation', email='old@example.test')
            category = apps.get_model('catalog', 'Category').objects.create(name='Hogar', slug='old-evaluation')
            publication = apps.get_model('publications', 'Publication').objects.create(
                submitter=user, category=category, title='Anterior', description='Anterior',
            )
            evaluation = apps.get_model('assistant', 'PublicationEvaluation').objects.create(
                publication=publication, status='completed', fingerprint='a' * 64,
                model='old-model', rules_version='old-rules', prompt_version='old-prompt',
                result={'historical': True}, input_tokens=100, output_tokens=50,
            )
            attempt = apps.get_model('assistant', 'EvaluationAttempt').objects.create(
                evaluation=evaluation, input_tokens=100, output_tokens=50, usage_known=True,
            )
            evaluation_id, attempt_id = evaluation.pk, attempt.pk
        finally:
            MigrationExecutor(connection).migrate(targets)
        from assistant.models import PublicationEvaluation, EvaluationAttempt

        preserved = PublicationEvaluation.objects.get(pk=evaluation_id)
        self.assertEqual(preserved.origin, 'review')
        self.assertEqual(preserved.content_fingerprint, '')
        self.assertIsNone(preserved.preparation_id)
        self.assertEqual(preserved.result, {'historical': True})
        self.assertEqual(EvaluationAttempt.objects.get(pk=attempt_id).input_tokens, 100)

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
