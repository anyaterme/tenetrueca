"""Scoped micropilot authorization, accounting and no-retry proofs; SDK always mocked."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from django.core.management import call_command, CommandError
from django.db import connection, close_old_connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from openai.types.responses.response_usage import ResponseUsage
from faq.models import FAQ

from chatbot.evaluation.dataset import load_dataset
from chatbot.evaluation.errors import EvaluationStopped
from chatbot.evaluation.fixtures import install_case
from chatbot.evaluation.ledger import open_run, reserve
from chatbot.evaluation.models import ChatEvaluationRun
from chatbot.evaluation.pricing import validate_tariff
from chatbot.evaluation.runner import evaluate
from chatbot.evaluation.selection import select_cases, execution_pairs
from chatbot.tests.test_evaluation_budget import options, price_data, usage, config
from chatbot.tests.test_openai_provider import response


SCOPE = ('E34', 'E36')
ORDER = ['E34:extractive', 'E36:extractive', 'E34:grounded', 'E36:grounded']


@override_settings(CHATBOT_EVAL_CASE_IDS=SCOPE)
class SelectionTests(SimpleTestCase):
    def setUp(self):
        self.cases, self.dataset_hash = load_dataset('pilot-v1')

    def test_exact_scope_preserves_versioned_cases_sources_and_hash(self):
        selected = select_cases(self.cases, options(case_ids=list(SCOPE), max_requests=4))
        self.assertEqual([c['id'] for c in selected], list(SCOPE))
        self.assertTrue(all(c is next(old for old in self.cases if old['id'] == c['id']) for c in selected))
        self.assertEqual(load_dataset('pilot-v1')[1], self.dataset_hash)
        self.assertEqual([f'{c["id"]}:{mode}' for c, mode in execution_pairs(selected, ['extractive', 'grounded'], scoped=True)], ORDER)

    def test_unknown_duplicate_empty_arbitrary_or_reordered_selection_is_rejected(self):
        for ids in ([], ['E34', 'E34'], ['E01'], ['E36', 'E34'], ['E99'], ['../E34'], 'E34,E36'):
            with self.subTest(ids=ids), self.assertRaises(EvaluationStopped):
                select_cases(self.cases, options(case_ids=ids, max_requests=4))

    def test_missing_selector_never_falls_back_to_ten_questions(self):
        with self.assertRaisesRegex(EvaluationStopped, 'case_selection_required'):
            select_cases(self.cases, options())

    def test_request_cap_cannot_exceed_number_of_authorized_pairs(self):
        with self.assertRaisesRegex(EvaluationStopped, 'case_selection_request_limit'):
            select_cases(self.cases, options(case_ids=list(SCOPE), max_requests=5))

    @override_settings(CHATBOT_EVAL_CASE_IDS=())
    def test_cli_alone_cannot_authorize_live_subset_but_can_preview_it(self):
        args = options(case_ids=list(SCOPE), max_requests=4, allow_live=True)
        with self.assertRaisesRegex(EvaluationStopped, 'case_selection_not_authorized'):
            select_cases(self.cases, args)
        self.assertEqual(len(select_cases(self.cases, {**args, 'dry_run': True})), 2)
        self.assertEqual(select_cases(self.cases, options()), self.cases)


@override_settings(CHATBOT_EVAL_CASE_IDS=SCOPE, CHATBOT_EVAL_MAX_REQUESTS=4)
class ScopedCommandTests(TestCase):
    def setUp(self):
        cases, _ = load_dataset('pilot-v1')
        install_case(next(c for c in cases if c['id'] == 'E34'))

    def test_dry_run_has_only_four_ordered_pairs_and_no_sdk_or_budget(self):
        with override_settings(CHATBOT_EVAL_PROJECT_REF='proj_private_do_not_export'), patch('chatbot.providers.AsyncOpenAI') as sdk:
            output = StringIO()
            call_command('evaluate_chatbot_live', case_ids=list(SCOPE), max_requests=4, dry_run=True, stdout=output)
            report = json.loads(output.getvalue())
            self.assertEqual(report['configuration']['execution_order'], ORDER)
            self.assertEqual([f'{r["case_id"]}:{r["mode"]}' for r in report['results']], ORDER)
            self.assertEqual(report['configuration']['cases'], list(SCOPE))
            self.assertEqual(report['planned_questions'], 2)
            self.assertEqual(report['maximum_possible_provider_calls'], 2)
            self.assertNotIn('proj_private_do_not_export', output.getvalue())
            sdk.assert_not_called()
        self.assertFalse(ChatEvaluationRun.objects.exists())

    def test_invalid_scope_or_cap_never_creates_client_or_ledger(self):
        for kwargs in ({'case_ids': ['E01'], 'max_requests': 4}, {'max_requests': 4},
                       {'case_ids': list(SCOPE), 'max_requests': 20}):
            with self.subTest(kwargs=kwargs), patch('chatbot.providers.AsyncOpenAI') as sdk, self.assertRaises(CommandError):
                call_command('evaluate_chatbot_live', stdout=StringIO(), **kwargs)
            sdk.assert_not_called()
        self.assertFalse(ChatEvaluationRun.objects.exists())


@skipUnless(connection.vendor == 'postgresql', 'Scoped live-path proofs require PostgreSQL; SDK is mocked.')
@override_settings(CHATBOT_EVAL_CASE_IDS=SCOPE, CHATBOT_EVAL_MAX_REQUESTS=4, CHATBOT_PROVIDER='openai',
                   CHATBOT_ENABLED=True, OPENAI_API_KEY='offline-only', CHATBOT_EVAL_PROJECT_REF='proj_synthetic',
                   CHATBOT_MAX_OUTPUT_TOKENS=400, CHATBOT_REQUEST_TIMEOUT=30)
class ScopedRunnerTests(TransactionTestCase):
    def setUp(self):
        cases, _ = load_dataset('pilot-v1')
        install_case(next(c for c in cases if c['id'] == 'E34'))
        # Exercise all four mocked paid paths using a synthetic editorial variant
        # outside the deliberately narrow local formatter. Frozen corpus is unchanged.
        faq = FAQ.objects.get(answer__startswith='Explora el catálogo')
        faq.answer = faq.answer.replace(' y ', '; ')
        faq.save()
        self.directory = TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        p = Path(self.directory.name)/'prices.json'; p.write_text(json.dumps(price_data()))
        override = override_settings(CHATBOT_EVAL_PRICING_FILE=str(p)); override.enable(); self.addCleanup(override.disable)
        for target in ('chatbot.evaluation.runner.require_evaluation_access',
                       'chatbot.evaluation.provider.require_evaluation_access', 'chatbot.providers.require_openai_access'):
            guard = patch(target); guard.start(); self.addCleanup(guard.stop)
        factory = patch('chatbot.providers.AsyncOpenAI'); self.factory = factory.start(); self.addCleanup(factory.stop)
        self.create = AsyncMock(side_effect=self.respond)
        self.factory.return_value.__aenter__.return_value.responses.create = self.create
        self.args = options(case_ids=list(SCOPE), max_requests=4, allow_live=True)

    async def respond(self, **args):
        sources = json.loads(args['input'][1]['content'].partition('\n')[2])['sources']
        question = args['input'][0]['content']
        overview = 'tres frases' in question
        item = next(f for f in sources if ('Explora el catálogo' if overview else 'Inicia sesión') in f['content'])
        texts = ('Explora el catálogo.', 'Presenta propuestas de objetos para revisión.',
                 'Consulta la ayuda para conocer el proceso de intercambio.') if overview else (
                 'Sign in and open the publication option.', 'Complete the object details and add photographs.',
                 'Submit the proposal to the team for review.')
        if args['text']['format']['name'] == 'trueca_grounded_answer':
            payload = {'answer': '\n\n'.join(texts), 'sufficient': True,
                       'claims': [{'text': t, 'evidence': [{'source_id': item['source_id'], 'quote': item['content']}]} for t in texts]}
        else:
            payload = {'selections': [{'source_id': item['source_id'], 'content': item['content'], 'display_text': ' '.join(texts)}]}
        return response(json.dumps(payload)).model_copy(update={'usage': ResponseUsage.model_validate(usage())})

    def test_four_requests_both_modes_fixed_scope_resume_and_project_privacy(self):
        result = evaluate(self.args)
        self.assertEqual(result['stop_reason'], 'completed')
        self.assertEqual(result['configuration']['cases'], list(SCOPE))
        self.assertEqual(result['configuration']['execution_order'], ORDER)
        self.assertNotIn('project', result['configuration'])
        self.assertEqual(set(result['results']), set(ORDER))
        self.assertEqual(result['send_intents'], 4)
        self.assertEqual(result['completed_provider_responses'], 4)
        self.assertTrue(all(not r['error'] for r in result['results'].values()))
        self.assertTrue(all(r['comparison_sources_equal'] for k, r in result['results'].items() if k.endswith(':grounded')))
        self.assertEqual(self.create.await_count, 4)
        again = evaluate({**self.args, 'resume': True})
        self.assertEqual(again['committed_usd'], result['committed_usd'])
        self.assertEqual(self.create.await_count, 4)
        with override_settings(CHATBOT_EVAL_CASE_IDS=('E34',)), self.assertRaisesRegex(EvaluationStopped, 'configuration_changed'):
            evaluate({**self.args, 'case_ids': ['E34'], 'max_requests': 2, 'resume': True})
        self.assertEqual(self.create.await_count, 4)

    def test_unknown_usage_preserves_reservation_and_stops_without_second_call(self):
        async def missing_usage(**args):
            return (await self.respond(**args)).model_copy(update={'usage': None})
        self.create.side_effect = missing_usage
        result = evaluate(self.args)
        self.assertEqual(result['send_intents'], 1)
        self.assertEqual(result['attempts'][0]['state'], 'unknown')
        self.assertEqual(result['committed_usd'], result['attempts'][0]['reserved_usd'])
        self.assertEqual(self.create.await_count, 1)

    def test_concurrent_scoped_pairs_admit_each_once_with_four_request_cap(self):
        data = {**price_data(), 'input_usd_per_million': '0.000001',
                'output_usd_per_million': '0.000001', 'special_input_usd_per_million': '0.000001'}
        tariff = validate_tariff(data, 'gpt-6-luna')
        cfg = {**config(tariff, requests=4), 'cases': list(SCOPE)}
        run = open_run(uuid4(), cfg)
        pairs = [(key, mode) for key in SCOPE for mode in ('extractive', 'grounded')]*2
        def claim(pair):
            close_old_connections()
            try:
                reserve(run.pk, *pair, tariff=tariff, input_ceiling=1050000, output_ceiling=400,
                        payload_hash='scoped-test', source_hash='same-source')
                return True
            except EvaluationStopped:
                return False
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=8) as pool:
            admitted = list(pool.map(claim, pairs))
        run.refresh_from_db()
        self.assertEqual(sum(admitted), 4)
        self.assertEqual(run.admitted_count, 4)
        self.assertEqual(run.attempts.count(), 4)
        self.assertLessEqual(run.committed_usd, Decimal('1.00'))
