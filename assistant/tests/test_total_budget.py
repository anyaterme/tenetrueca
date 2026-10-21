from datetime import timedelta
from decimal import Decimal
from queue import Queue
from threading import local
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from assistant.models import EvaluationAttempt, OpenAIUsageDay
from assistant.providers import ProviderResponse
from assistant.services import BudgetExceeded, enqueue_evaluation, record_usage, reserve_attempt
from assistant.tests import test_concurrency as concurrency_tests
from catalog.models import Category
from publications.models import Publication


class BudgetFixture:
    def setUp(self):
        user = get_user_model().objects.create_user(username='total-budget', email='budget@example.test')
        category = Category.objects.create(name='Hogar', slug='total-budget')
        publication = Publication.objects.create(
            submitter=user, category=category, title='Prueba de presupuesto', description='Objeto.',
            status='pending_review', submitted_at=timezone.now(),
        )
        self.evaluation = enqueue_evaluation(publication.pk)[0]


@override_settings(OPENAI_EVALUATION_BACKEND='mock', OPENAI_MODEL='fixture-model',
                   OPENAI_INPUT_USD_PER_MILLION=Decimal('1'),
                   OPENAI_OUTPUT_USD_PER_MILLION=Decimal('2'),
                   OPENAI_REQUEST_RESERVE_USD=Decimal('.1'),
                   OPENAI_DAILY_BUDGET_USD=Decimal('10'), OPENAI_DAILY_REQUEST_LIMIT=100)
class TotalBudgetConfigurationTests(BudgetFixture, TestCase):
    def test_invalid_total_cap_blocks_before_creating_a_reservation(self):
        for cap in ('0', '-1', 'NaN', 'Infinity'):
            with self.subTest(cap=cap), override_settings(OPENAI_TOTAL_BUDGET_USD=Decimal(cap)):
                with self.assertRaises(BudgetExceeded):
                    reserve_attempt(self.evaluation, simulated=False)
        self.assertFalse(EvaluationAttempt.objects.exists())
        self.assertFalse(OpenAIUsageDay.objects.exists())

    @override_settings(OPENAI_TOTAL_BUDGET_USD=Decimal('1'))
    def test_unsupported_database_fails_closed(self):
        with patch.object(connection, 'vendor', 'sqlite'):
            with self.assertRaisesMessage(BudgetExceeded, 'requiere PostgreSQL'):
                reserve_attempt(self.evaluation, simulated=False)
        self.assertFalse(EvaluationAttempt.objects.exists())

    @override_settings(OPENAI_TOTAL_BUDGET_USD=Decimal('0'))
    def test_mock_remains_available_when_real_budget_is_disabled(self):
        attempt = reserve_attempt(self.evaluation, simulated=True)
        self.assertTrue(attempt.is_simulated)
        self.assertEqual(attempt.reserved_usd, 0)
        self.assertFalse(OpenAIUsageDay.objects.exists())


@skipUnless(connection.vendor == 'postgresql', 'El límite acumulado usa bloqueo de PostgreSQL.')
@override_settings(OPENAI_EVALUATION_BACKEND='mock', OPENAI_MODEL='fixture-model',
                   OPENAI_INPUT_USD_PER_MILLION=Decimal('1'),
                   OPENAI_OUTPUT_USD_PER_MILLION=Decimal('2'),
                   OPENAI_REQUEST_RESERVE_USD=Decimal('.1'), OPENAI_TOTAL_BUDGET_USD=Decimal('1'),
                   OPENAI_DAILY_BUDGET_USD=Decimal('10'), OPENAI_DAILY_REQUEST_LIMIT=100)
class TotalBudgetPostgreSQLTests(BudgetFixture, TransactionTestCase):
    race = concurrency_tests.EvaluationConcurrencyTests.race

    def historical_attempt(self, *, cost='0', reserved='0', known=True, simulated=False):
        return EvaluationAttempt.objects.create(
            evaluation=self.evaluation, is_simulated=simulated, usage_known=known,
            estimated_cost_usd=Decimal(cost), reserved_usd=Decimal(reserved),
            created_at=timezone.now() - timedelta(days=2),
        )

    def test_previous_dates_known_costs_block_new_reservations(self):
        self.historical_attempt(cost='.95')
        with self.assertRaisesMessage(BudgetExceeded, 'acumulado'):
            reserve_attempt(self.evaluation, simulated=False)
        self.assertEqual(EvaluationAttempt.objects.count(), 1)
        self.assertFalse(OpenAIUsageDay.objects.exists())

    def test_unknown_usage_keeps_the_full_reservation(self):
        self.historical_attempt(reserved='.95', known=False)
        with self.assertRaises(BudgetExceeded):
            reserve_attempt(self.evaluation, simulated=False)
        self.assertEqual(EvaluationAttempt.objects.count(), 1)

    def test_known_usage_replaces_reservation_and_mock_costs_are_excluded(self):
        self.historical_attempt(cost='.02', reserved='.95')
        self.historical_attempt(cost='50', reserved='50', simulated=True)
        reserve_attempt(self.evaluation, simulated=False)
        self.assertEqual(OpenAIUsageDay.objects.get().requests, 1)

    def test_exact_cap_allows_one_reservation_then_blocks(self):
        self.historical_attempt(cost='.9')
        reserve_attempt(self.evaluation, simulated=False)
        with self.assertRaises(BudgetExceeded):
            reserve_attempt(self.evaluation, simulated=False)
        self.assertEqual(OpenAIUsageDay.objects.get().requests, 1)

    @override_settings(OPENAI_TOTAL_BUDGET_USD=Decimal('.15'))
    def test_changing_utc_day_does_not_reset_total_cap(self):
        reserve_attempt(self.evaluation, simulated=False)
        tomorrow = timezone.now() + timedelta(days=1)
        with patch('assistant.services.timezone.now', return_value=tomorrow):
            with self.assertRaises(BudgetExceeded):
                reserve_attempt(self.evaluation, simulated=False)
        self.assertEqual(OpenAIUsageDay.objects.count(), 1)

    @override_settings(OPENAI_TOTAL_BUDGET_USD=Decimal('.15'))
    def test_recorded_usage_releases_unused_reservation(self):
        first = reserve_attempt(self.evaluation, simulated=False)
        with self.assertRaises(BudgetExceeded):
            reserve_attempt(self.evaluation, simulated=False)
        record_usage(first, ProviderResponse(payload={}, input_tokens=1000))
        reserve_attempt(self.evaluation, simulated=False)
        self.assertEqual(OpenAIUsageDay.objects.get().reserved_usd, Decimal('.101'))

    @override_settings(OPENAI_TOTAL_BUDGET_USD=Decimal('.15'))
    def test_concurrent_requests_cannot_exceed_total_cap(self):
        def reserve():
            try:
                reserve_attempt(self.evaluation, simulated=False)
                return True
            except BudgetExceeded:
                return False

        self.assertEqual(sorted(self.race(reserve)), [False, True])
        self.assertEqual(EvaluationAttempt.objects.count(), 1)
        self.assertEqual(OpenAIUsageDay.objects.get().requests, 1)

    @override_settings(OPENAI_TOTAL_BUDGET_USD=Decimal('.15'))
    def test_concurrent_reservations_for_different_days_share_the_cap(self):
        clocks = Queue()
        clocks.put(timezone.now())
        clocks.put(timezone.now() + timedelta(days=1))
        thread_clock = local()

        def reserve():
            thread_clock.now = clocks.get_nowait()
            try:
                reserve_attempt(self.evaluation, simulated=False)
                return True
            except BudgetExceeded:
                return False

        with patch('assistant.services.timezone.now', side_effect=lambda: thread_clock.now):
            self.assertEqual(sorted(self.race(reserve)), [False, True])
        self.assertEqual(EvaluationAttempt.objects.count(), 1)
        self.assertEqual(OpenAIUsageDay.objects.count(), 1)
