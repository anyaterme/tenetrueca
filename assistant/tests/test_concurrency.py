import io
import tempfile
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile

from assistant.fixtures import simulated_response
from assistant.models import EvaluationAttempt, OpenAIUsageDay, PublicationEvaluation
from assistant.providers import ProviderResponse
from assistant.services import BudgetExceeded, enqueue_evaluation, process_evaluation, reserve_attempt
from catalog.models import Category
from publications.models import Publication, PublicationPhoto


@skipUnless(connection.vendor == 'postgresql', 'La concurrencia requiere bloqueos reales de PostgreSQL.')
@override_settings(OPENAI_EVALUATION_BACKEND='mock', OPENAI_MODEL='fixture-model')
class EvaluationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory(prefix='trueca-ai-concurrent-')
        self.addCleanup(self.media.cleanup)
        override = override_settings(PRIVATE_MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        citizen = get_user_model().objects.create_user(username='race', email='race@example.com')
        category = Category.objects.create(name='Deporte', slug='race-deporte')
        self.publication = Publication.objects.create(
            submitter=citizen, category=category, title='Pelota', description='Pelota deportiva.',
            status='pending_review', submitted_at=timezone.now(),
        )
        buffer = io.BytesIO()
        Image.new('RGB', (50, 50)).save(buffer, format='JPEG')
        PublicationPhoto.objects.create(publication=self.publication,
                                       image=SimpleUploadedFile('race.jpg', buffer.getvalue()))

    def race(self, function):
        barrier = Barrier(2)

        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return function()
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run) for _ in range(2)]
            return [future.result(timeout=20) for future in futures]

    def test_concurrent_enqueues_create_one_job(self):
        results = self.race(lambda: enqueue_evaluation(self.publication.pk)[0].pk)
        self.assertEqual(results[0], results[1])
        self.assertEqual(PublicationEvaluation.objects.count(), 1)

    def test_two_workers_only_invoke_provider_once(self):
        evaluation = enqueue_evaluation(self.publication.pk)[0]
        provider = Mock(is_simulated=True)
        provider.evaluate.return_value = ProviderResponse(simulated_response('suitable'))
        self.race(lambda: process_evaluation(evaluation.pk, provider=provider))
        self.assertEqual(provider.evaluate.call_count, 1)
        self.assertEqual(EvaluationAttempt.objects.count(), 1)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'completed')

    @override_settings(OPENAI_INPUT_USD_PER_MILLION=Decimal('1'),
                       OPENAI_OUTPUT_USD_PER_MILLION=Decimal('2'),
                       OPENAI_REQUEST_RESERVE_USD=Decimal('.1'),
                       OPENAI_DAILY_BUDGET_USD=Decimal('.1'), OPENAI_DAILY_REQUEST_LIMIT=1)
    def test_concurrent_reservations_cannot_overspend(self):
        evaluation = enqueue_evaluation(self.publication.pk)[0]

        def reserve():
            try:
                reserve_attempt(evaluation, simulated=False)
                return True
            except BudgetExceeded:
                return False

        self.assertEqual(sorted(self.race(reserve)), [False, True])
        self.assertEqual(OpenAIUsageDay.objects.get().requests, 1)
        self.assertEqual(EvaluationAttempt.objects.count(), 1)
