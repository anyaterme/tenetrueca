"""No tokenizer or remote counter is trusted as an undocumented billing ceiling."""
from copy import deepcopy
from dataclasses import replace
from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from chatbot.evaluation.errors import EvaluationStopped
from chatbot.evaluation.models import ChatEvaluationRun
from chatbot.evaluation.pricing import validate_tariff
from chatbot.evaluation.token_budget import input_bound, MAX_PAYLOAD_BYTES
from chatbot.providers import ChatTurn, request_arguments
from chatbot.tests.fixtures import knowledge_fixture
from chatbot.tests.test_evaluation_budget import price_data


def arguments(question='Hola', *, language='es', mode='extractive'):
    knowledge = replace(knowledge_fixture('Información pública de prueba. Café, niños, ♻️.'), generation_mode=mode)
    result = request_arguments([ChatTurn('user', question)], language=language, knowledge=knowledge)
    return {**result, 'service_tier': 'default', 'truncation': 'disabled'}


@override_settings(CHATBOT_EVAL_TOKEN_BOUND_METHOD='conservative', CHATBOT_OPENAI_MODEL='gpt-6-luna',
                   CHATBOT_MAX_OUTPUT_TOKENS=400, CHATBOT_EVAL_MAX_OUTPUT_TOKENS=400,
                   CHATBOT_EVAL_MAX_INPUT_TOKENS=1050000)
class TokenBudgetTests(SimpleTestCase):
    def bound(self, payload=None, limit=1050000):
        return input_bound(payload or arguments(), max_input_tokens=limit)

    def test_short_maximum_unicode_and_both_languages_keep_the_full_safe_bound(self):
        for language in ('es', 'en'):
            for question in ('x', 'x' * 2000, 'áñ👩🏽‍💻漢字<>\\\"\n' * 100):
                for mode in ('extractive', 'grounded'):
                    with self.subTest(language=language, mode=mode, size=len(question)):
                        bound = self.bound(arguments(question, language=language, mode=mode))
                        self.assertEqual(bound.input_tokens, 1050000)
                        self.assertEqual(bound.output_tokens, 400)
                        self.assertFalse(bound.adjusted_bound_available)
                        self.assertGreater(bound.payload_bytes, sum(bound.component_bytes.values()))

    def test_empty_and_maximum_context_change_payload_hash_not_the_guaranteed_bound(self):
        small, large = arguments(), arguments()
        small['input'][1]['content'] = ''
        large['input'][1]['content'] = '漢' * 6000
        a, b = self.bound(small), self.bound(large)
        self.assertEqual(a.input_tokens, b.input_tokens)
        self.assertNotEqual(a.payload_hash, b.payload_hash)
        self.assertLess(a.payload_bytes, b.payload_bytes)

    def test_instructions_and_entire_structured_schema_are_in_the_fingerprint(self):
        original = arguments()
        for key in ('instructions', 'text'):
            payload = deepcopy(original)
            if key == 'instructions':
                payload[key] += '\nInstrucciones verificadas. ' * 100
            else:
                payload[key]['format']['schema']['properties']['additional_test_field'] = {'type': 'string'}
            result = self.bound(payload)
            self.assertNotEqual(result.payload_hash, self.bound(original).payload_hash)
            self.assertEqual(result.input_tokens, 1050000)

    def test_adjusted_bound_or_unknown_tokenizer_is_never_enabled_by_a_setting(self):
        for method, code in [('verified', 'verified_input_bound_unavailable'), ('tiktoken', 'unknown_token_bound_method')]:
            with self.subTest(method=method), override_settings(CHATBOT_EVAL_TOKEN_BOUND_METHOD=method):
                with self.assertRaisesRegex(EvaluationStopped, code):
                    self.bound()
        with override_settings(CHATBOT_EVAL_TOKEN_BOUND_METHOD='auto'):
            self.assertEqual(self.bound().method, 'documented_full_context_ceiling')

    def test_unknown_model_sdk_and_remote_or_tool_input_are_rejected(self):
        payload = arguments(); payload['model'] = 'unreviewed-model'
        with self.assertRaisesRegex(EvaluationStopped, 'model_not_authorized'):
            self.bound(payload)
        with patch('chatbot.evaluation.token_budget.openai.__version__', '99.0.0'):
            with self.assertRaisesRegex(EvaluationStopped, 'unsupported_budget_sdk'):
                self.bound()
        for change in ({'tools': []}, {'previous_response_id': 'private-history'}, {'truncation': 'auto'},
                       {'input': [{'role': 'user', 'content': [{'type': 'input_image', 'image_url': 'https://invalid.test'}]}]}):
            with self.subTest(change=change), self.assertRaises(EvaluationStopped):
                self.bound({**arguments(), **change})

    @override_settings(CHATBOT_EVAL_TOKEN_BOUND_METHOD='provider_counted')
    def test_provider_count_reduces_only_a_verified_full_faq_bound(self):
        from chatbot.full_faq_protocol import CACHE_BREAKPOINT, CACHE_OPTIONS

        payload = arguments()
        payload['input'][0]['content'] = [{'type': 'input_text', 'text': 'Evidence',
                                           'prompt_cache_breakpoint': CACHE_BREAKPOINT}]
        payload['prompt_cache_options'] = CACHE_OPTIONS
        preview = self.bound(payload, limit=8192)
        measured = input_bound(payload, max_input_tokens=8192, measured_input_tokens=3000)
        self.assertEqual(preview.method, 'provider_count_required')
        self.assertEqual(preview.payload_hash, measured.payload_hash)
        self.assertEqual(measured.input_tokens, 3512)
        self.assertEqual(measured.measured_input_tokens, 3000)
        with self.assertRaisesRegex(EvaluationStopped, 'provider_count_outside_budget'):
            input_bound(payload, max_input_tokens=8192, measured_input_tokens=8192)

    def test_input_and_output_caps_and_oversized_payload_fail_before_sdk(self):
        for limit in (1000, 1049999, 1050001):
            with self.subTest(limit=limit), self.assertRaises(EvaluationStopped):
                self.bound(limit=limit)
        for output in (0, 401, True):
            with self.subTest(output=output), self.assertRaises(EvaluationStopped):
                self.bound({**arguments(), 'max_output_tokens': output})
        with self.assertRaisesRegex(EvaluationStopped, 'evaluation_payload_too_large'):
            self.bound({**arguments(), 'instructions': 'x' * MAX_PAYLOAD_BYTES})

    def test_long_context_rates_and_margin_apply_even_to_a_tiny_question(self):
        prices = {**price_data(), 'input_usd_per_million': '0.20', 'output_usd_per_million': '0.75',
                  'special_input_usd_per_million': '0.50'}  # Fabricated conservative test profile.
        tariff = validate_tariff(prices, 'gpt-6-luna')
        bound = self.bound(arguments('x'))
        reserve = tariff.cost(bound.input_tokens, bound.output_tokens, margin=True)
        self.assertEqual(str(reserve), '0.808830000')
        self.assertGreater(reserve, tariff.cost(bound.input_tokens, bound.output_tokens))
        for scope in (None, 'short_context_only'):
            with self.assertRaises(EvaluationStopped):
                validate_tariff({**prices, 'rate_scope': scope}, 'gpt-6-luna')


class TokenBudgetDryRunTests(TestCase):
    def test_preview_exposes_limits_and_does_not_create_a_budget_or_sdk_client(self):
        from chatbot.evaluation.fixtures import install_case
        from chatbot.evaluation.dataset import load_dataset
        cases, _ = load_dataset('pilot-v1')
        install_case(cases[0])
        output = StringIO()
        with override_settings(CHATBOT_EVAL_PRICING_FILE=''), patch('chatbot.providers.AsyncOpenAI') as sdk:
            call_command('evaluate_chatbot_live', dry_run=True, stdout=output)
            sdk.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual(report['planned_questions'], 10)
        self.assertEqual(report['planned_question_modes'], 20)
        self.assertLessEqual(report['maximum_possible_provider_calls'], 20)
        self.assertEqual(report['pricing_admission_blocker'], 'pricing_missing')
        self.assertFalse(report['adjusted_bound_available'])
        generated = [r for r in report['results'] if r['would_generate']]
        self.assertTrue(generated)
        self.assertTrue(all(r['input_bound']['input_tokens'] == 1050000 for r in generated))
        self.assertTrue(all(r['maximum_reserve_usd'] is None for r in generated))
        self.assertFalse(ChatEvaluationRun.objects.exists())
