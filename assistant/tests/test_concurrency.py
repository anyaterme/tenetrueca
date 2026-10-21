import io
import tempfile
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from queue import Queue
from uuid import uuid4
from unittest import skipUnless
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile

from assistant.fixtures import simulated_response
from assistant.models import EvaluationAttempt, OpenAIUsageDay, PublicationEvaluation, PublicationPreparation
from assistant.preparations import start_preparation, run_preanalysis, confirm_preparation, digest
from assistant.providers import ProviderResponse
from assistant.services import BudgetExceeded, enqueue_evaluation, process_evaluation, reserve_attempt
from catalog.models import Category
from publications.models import Publication, PublicationPhoto
from publications.forms import CitizenPublicationForm


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


@skipUnless(connection.vendor == 'postgresql', 'La concurrencia requiere bloqueos reales de PostgreSQL.')
@override_settings(OPENAI_EVALUATION_BACKEND='mock', OPENAI_MODEL='fixture-model')
class PreparationConcurrencyTests(TransactionTestCase):
    race = EvaluationConcurrencyTests.race

    def setUp(self):
        self.media = tempfile.TemporaryDirectory(prefix='trueca-preanalysis-concurrent-')
        self.addCleanup(self.media.cleanup)
        override = override_settings(PRIVATE_MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.user = get_user_model().objects.create_user(username='pre-race', email='pre-race@example.test')
        self.category = Category.objects.create(name='Hogar', slug='pre-race-hogar')

    def prepare(self, key=None):
        buffer = io.BytesIO()
        Image.new('RGB', (50, 50)).save(buffer, format='JPEG')
        form = CitizenPublicationForm({
            'title': 'Objeto de prueba', 'description': 'Descripción original.',
            'parent_category': self.category.pk,
        }, {'photos': SimpleUploadedFile('race.jpg', buffer.getvalue())}, require_photo=True)
        assert form.is_valid(), form.errors
        return start_preparation(self.user, form, key or uuid4())

    def analyze(self, preparation):
        provider = Mock(is_simulated=True)
        provider.evaluate.return_value = ProviderResponse(simulated_response('suitable'))
        run_preanalysis(preparation.pk, provider=provider)
        return provider

    def test_concurrent_initial_posts_share_one_preparation_and_one_analysis(self):
        key = uuid4()
        preparations = self.race(lambda: self.prepare(key).pk)
        self.assertEqual(preparations[0], preparations[1])
        self.assertEqual(PublicationPreparation.objects.count(), 1)
        preparation = PublicationPreparation.objects.get()
        provider = Mock(is_simulated=True)
        provider.evaluate.return_value = ProviderResponse(simulated_response('suitable'))
        self.race(lambda: run_preanalysis(preparation.pk, provider=provider))
        provider.evaluate.assert_called_once()
        self.assertEqual(EvaluationAttempt.objects.count(), 1)

    def test_concurrent_confirmations_create_only_one_publication(self):
        preparation = self.prepare()
        self.analyze(preparation)
        results = self.race(lambda: confirm_preparation(
            preparation.pk, self.user, preparation.original_data,
            expected_version=digest(preparation.working_data),
        ).pk)
        self.assertEqual(results[0], results[1])
        self.assertEqual(Publication.objects.count(), 1)

    @override_settings(PUBLICATION_DAILY_LIMIT=1)
    def test_concurrent_distinct_preparations_cannot_exceed_submission_limit(self):
        preparations = [self.prepare(), self.prepare()]
        for preparation in preparations:
            self.analyze(preparation)
        # Each racing thread takes a different preparation; the owner lock serializes
        # the daily-limit check and the creation of the final submission.
        queue = Queue()
        for preparation in preparations:
            queue.put(preparation)

        def confirm():
            preparation = queue.get_nowait()
            try:
                confirm_preparation(preparation.pk, self.user, preparation.original_data,
                                    expected_version=digest(preparation.working_data))
                return True
            except ValidationError:
                return False

        self.assertEqual(sorted(self.race(confirm)), [False, True])
        self.assertEqual(Publication.objects.count(), 1)
