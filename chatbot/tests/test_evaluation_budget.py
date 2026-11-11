"""Budget proofs use fabricated prices and mocked SDK usage, never account billing."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import skipUnless
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
from django.core.management import call_command, CommandError
from django.db import connection, close_old_connections, DatabaseError
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from openai import APITimeoutError, APIStatusError, RateLimitError

from chatbot.evaluation.access import limits
from chatbot.evaluation.dataset import load_dataset
from chatbot.evaluation.errors import EvaluationStopped
from chatbot.evaluation.ledger import (open_run, reserve, mark_sent, settle, execution_lock, record_result)
from chatbot.evaluation.models import ChatEvaluationRun, ChatEvaluationAttempt
from chatbot.evaluation.output import atomic_json
from chatbot.evaluation.pricing import validate_tariff, load_tariff, money, MODEL_CEILINGS
from chatbot.evaluation.provider import BudgetedProvider
from chatbot.evaluation.runner import evaluate
from chatbot.providers import ChatTurn
from chatbot.tests.fixtures import knowledge_fixture, selection_json
from chatbot.tests.test_openai_provider import response


def price_data():
    # Deliberately fabricated rates for arithmetic tests. Not operator authorization.
    return {'approved': True, 'coverage_approved': True, 'model': 'gpt-6-luna',
        'response_models': ['gpt-6-luna'], 'currency': 'USD', 'service_tier': 'default',
        'input_usd_per_million': '0.10', 'output_usd_per_million': '0.50',
        'special_input_usd_per_million': '0.375', 'fixed_request_usd': '0', 'safety_margin': '1.10',
        'verified_on': date.today().isoformat(), 'source': 'https://developers.openai.com/api/docs/pricing',
        'model_source': 'https://developers.openai.com/api/docs/models/gpt-6-luna',
        'billing_coverage': 'all_text_input_output_cache_writes_long_context_and_surcharges',
        'bound_strategy': 'documented_full_context_ceiling', 'rate_scope': 'maximum_across_entire_context_window',
        'context_tokens': 1050000}


def usage(inputs=100, outputs=10):
    return {'input_tokens': inputs, 'output_tokens': outputs, 'total_tokens': inputs+outputs,
            'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}}


def config(tariff, budget='1.00', requests=20):
    return {'max_budget_usd': budget, 'max_requests': requests, 'model': tariff.model,
            'max_input_tokens': tariff.input_ceiling, 'max_output_tokens': 400,
            'modes': ['extractive', 'grounded'], 'tariff_hash': tariff.signature,
            'cases': [f'E{i:02d}' for i in range(1, 91)]}


def options(**kw):
    return {'allow_live': False, 'dry_run': False, 'resume': False, 'run_id': str(uuid4()),
            'dataset': 'pilot-v1', 'model': 'gpt-6-luna', 'mode': 'both', 'max_requests': 20,
            'max_budget_usd': '1.00', 'max_input_tokens': 1050000, 'max_output_tokens': 400, **kw}


class TariffTests(SimpleTestCase):
    def test_missing_price_and_unapproved_file_fail_closed(self):
        with self.assertRaisesRegex(EvaluationStopped, 'pricing_missing'):
            load_tariff('', 'gpt-6-luna')
        with self.assertRaisesRegex(EvaluationStopped, 'pricing_unapproved'):
            validate_tariff({**price_data(), 'approved': False}, 'gpt-6-luna')

    def test_stale_future_incomplete_wrong_currency_and_unknown_model_are_rejected(self):
        variations = [{'verified_on': (date.today()-timedelta(days=8)).isoformat()},
                      {'verified_on': (date.today()+timedelta(days=1)).isoformat()},
                      {'coverage_approved': False}, {'billing_coverage': 'input_only'},
                      {'currency': 'EUR'}, {'source': 'https://untrusted.invalid/prices'},
                      {'model': 'another-model'}, {'context_tokens': 10000},
                      {'special_input_usd_per_million': None}, {'special_input_usd_per_million': '0'}, {'safety_margin': '1.0'},
                      {'input_usd_per_million': float(.1)}, {'input_usd_per_million': 'NaN'}]
        for variation in variations:
            with self.subTest(variation=variation), self.assertRaises(EvaluationStopped):
                validate_tariff({**price_data(), **variation}, 'gpt-6-luna')
        with self.assertRaises(EvaluationStopped):
            validate_tariff(price_data(), 'unpriced-model')

    def test_conservative_rounding_and_special_cost_coverage(self):
        tariff = validate_tariff(price_data(), 'gpt-6-luna')
        self.assertEqual(money(Decimal('.00000000001')), Decimal('.000000001'))
        self.assertEqual(tariff.cost(100, 10), Decimal('.000052500'))
        self.assertGreater(tariff.cost(100, 10, margin=True), tariff.cost(100, 10))
        self.assertGreater(tariff.cost(tariff.input_ceiling, 400, margin=True), Decimal('.50'))

    def test_no_character_token_conversion_or_unknown_context_profile_is_used(self):
        tariff = validate_tariff(price_data(), 'gpt-6-luna')
        self.assertEqual(tariff.input_ceiling, MODEL_CEILINGS['gpt-6-luna'])
        self.assertEqual(tariff.cost(tariff.input_ceiling, 400), tariff.cost(1050000, 400))

    def test_cli_flags_cannot_raise_maximum_or_supply_float_nan(self):
        for kw in ({'max_requests':21}, {'max_budget_usd':'1.01'}, {'max_budget_usd':'NaN'},
                   {'max_budget_usd':'0'}, {'max_budget_usd':'.9999999999'}, {'max_input_tokens':1050001}, {'max_output_tokens':401},
                   {'max_requests':0}, {'max_output_tokens':0}, {'model':'unauthorized'}):
            with self.subTest(kw=kw), self.assertRaises(EvaluationStopped):
                limits(options(**kw))


class LedgerTests(TestCase):
    def setUp(self):
        self.tariff = validate_tariff(price_data(), 'gpt-6-luna')
        self.config = config(self.tariff)
        self.run = open_run(uuid4(), self.config)

    def admit(self, case='E01', mode='extractive', **kwargs):
        return reserve(self.run.pk, case, mode, tariff=self.tariff, input_ceiling=self.tariff.input_ceiling,
                       output_ceiling=400, payload_hash='a'*64, source_hash=kwargs.pop('source_hash','b'*64), **kwargs)

    def test_sufficient_budget_is_reserved_before_send(self):
        attempt = self.admit()
        self.run.refresh_from_db()
        self.assertEqual(self.run.committed_usd, attempt.reserved_usd)
        self.assertEqual((self.run.admitted_count,self.run.sent_count), (1,0))
        mark_sent(attempt.pk)
        self.run.refresh_from_db()
        self.assertEqual(self.run.sent_count,1)

    def test_insufficient_global_and_remaining_budget_block_admission(self):
        small = open_run(uuid4(), config(self.tariff, budget='.10'))
        with self.assertRaisesRegex(EvaluationStopped,'budget_insufficient'):
            reserve(small.pk,'E01','extractive',tariff=self.tariff,input_ceiling=1050000,
                    output_ceiling=400,payload_hash='a'*64,source_hash='b'*64)
        self.admit()
        with self.assertRaisesRegex(EvaluationStopped,'budget_insufficient'):
            self.admit('E02')
        small.refresh_from_db()
        self.assertEqual(small.admitted_count,0)

    def test_known_usage_releases_only_justified_difference(self):
        attempt=self.admit(); mark_sent(attempt.pk)
        settle(attempt.pk, tariff=self.tariff, usage=usage(), model=self.tariff.model, completed=True)
        self.run.refresh_from_db(); attempt.refresh_from_db()
        self.assertEqual(self.run.committed_usd,self.tariff.cost(100,10))
        self.assertEqual(self.run.completed_count,1)
        self.assertEqual(attempt.state,'settled')
        self.assertLess(attempt.charged_usd,attempt.reserved_usd)

    def test_provider_counted_run_requires_the_verified_count_and_retains_unknown_usage(self):
        run = open_run(uuid4(), {**self.config, 'modes': ['full_faq'], 'max_input_tokens': 8192,
                                 'token_bound_method': 'provider_counted'})
        arguments = {'tariff': self.tariff, 'input_ceiling': 3512, 'output_ceiling': 400,
                     'payload_hash': 'a'*64, 'source_hash': 'b'*64}
        with self.assertRaisesRegex(EvaluationStopped, 'unreliable_cost_bound'):
            reserve(run.pk, 'E01', 'full_faq', **arguments)
        attempt = reserve(run.pk, 'E01', 'full_faq', **arguments, measured_input_tokens=3000)
        self.assertEqual(attempt.reserved_usd, self.tariff.cost(3512, 400, margin=True))
        mark_sent(attempt.pk)
        settle(attempt.pk, tariff=self.tariff)
        run.refresh_from_db()
        self.assertEqual(run.committed_usd, attempt.reserved_usd)
        self.assertEqual(run.stop_reason, 'usage_unreliable')

    def test_twenty_attempts_maximum_even_after_all_usage_releases(self):
        for i in range(1,21):
            attempt=self.admit(f'E{i:02d}'); mark_sent(attempt.pk)
            settle(attempt.pk,tariff=self.tariff,usage=usage(),model=self.tariff.model,completed=True)
            self.run.refresh_from_db()
            self.assertLessEqual(self.run.committed_usd,Decimal('1'))
            self.assertLessEqual(self.run.peak_committed_usd,Decimal('1'))
        with self.assertRaisesRegex(EvaluationStopped,'request_limit'):
            self.admit('E21')
        self.run.refresh_from_db()
        self.assertEqual((self.run.admitted_count,self.run.sent_count), (20,20))

    def test_unknown_usage_timeout_preserves_entire_reservation(self):
        attempt=self.admit(); mark_sent(attempt.pk)
        settle(attempt.pk,tariff=self.tariff)
        self.run.refresh_from_db();attempt.refresh_from_db()
        self.assertEqual(self.run.committed_usd,attempt.reserved_usd)
        self.assertEqual(attempt.state,'unknown')
        self.assertEqual(self.run.completed_count,0)

    def test_bad_usage_wrong_model_extra_component_or_above_bounds_never_release(self):
        for provided,model in [(usage(1050001,1),self.tariff.model),(usage(1,401),self.tariff.model),
                               ({**usage(),'surprise_billable_tokens':10},self.tariff.model),
                               (usage(),'different-model'),({**usage(),'total_tokens':0},self.tariff.model)]:
            with self.subTest(provided=provided):
                run=open_run(uuid4(),self.config)
                attempt=reserve(run.pk,'E01','extractive',tariff=self.tariff,input_ceiling=1050000,output_ceiling=400,
                                payload_hash='a'*64,source_hash='b'*64)
                mark_sent(attempt.pk);settle(attempt.pk,tariff=self.tariff,usage=provided,model=model)
                attempt.refresh_from_db();run.refresh_from_db()
                self.assertEqual(attempt.state,'unknown');self.assertEqual(run.committed_usd,attempt.reserved_usd)

    def test_duplicate_admission_and_double_send_and_settlement_are_terminal(self):
        attempt=self.admit();mark_sent(attempt.pk)
        with self.assertRaises(EvaluationStopped): self.admit()
        with self.assertRaises(EvaluationStopped): mark_sent(attempt.pk)
        settle(attempt.pk,tariff=self.tariff,usage=usage(),model=self.tariff.model,completed=True)
        first=ChatEvaluationRun.objects.get(pk=self.run.pk).committed_usd
        settle(attempt.pk,tariff=self.tariff,usage=usage(0,0),model=self.tariff.model,completed=True)
        self.run.refresh_from_db();self.assertEqual(self.run.committed_usd,first)
        self.assertEqual(self.run.completed_count,1)

    def test_resume_does_not_reset_budget_and_never_retries_interrupted_tuple(self):
        attempt=self.admit();mark_sent(attempt.pk)
        recovered=open_run(self.run.pk,self.config,resume=True)
        attempt.refresh_from_db()
        self.assertEqual(attempt.state,'unknown');self.assertEqual(recovered.committed_usd,attempt.reserved_usd)
        with self.assertRaisesRegex(EvaluationStopped,'attempt_already_admitted'):self.admit()
        with self.assertRaisesRegex(EvaluationStopped,'execution_exists'):open_run(self.run.pk,self.config)

    def test_restart_between_reservation_and_send_is_also_conservative(self):
        attempt=self.admit()
        open_run(self.run.pk,self.config,resume=True)
        attempt.refresh_from_db();self.assertEqual(attempt.state,'unknown')
        self.assertEqual(ChatEvaluationRun.objects.get(pk=self.run.pk).committed_usd,attempt.reserved_usd)

    def test_configuration_or_source_version_changes_block_resume_comparison(self):
        with self.assertRaisesRegex(EvaluationStopped,'configuration_changed'):
            open_run(self.run.pk,{**self.config,'max_requests':19},resume=True)
        attempt=self.admit();mark_sent(attempt.pk)
        settle(attempt.pk,tariff=self.tariff,usage=usage(),model=self.tariff.model)
        with self.assertRaisesRegex(EvaluationStopped,'source_versions_changed'):
            self.admit(mode='grounded',source_hash='c'*64)
        record_result(self.run.pk, 'E02:extractive', {'input_source_hash': 'd'*64, 'provider_attempted': False})
        with self.assertRaisesRegex(EvaluationStopped, 'source_versions_changed'):
            self.admit('E02', mode='grounded')

    def test_missing_bound_or_over_output_or_unregistered_case_are_rejected(self):
        for inputs,outputs,case in [(100,400,'E01'),(1050000,401,'E01'),(1050000,400,'INVENTED')]:
            with self.subTest(inputs=inputs,outputs=outputs),self.assertRaises(EvaluationStopped):
                reserve(self.run.pk,case,'extractive',tariff=self.tariff,input_ceiling=inputs,
                        output_ceiling=outputs,payload_hash='a'*64,source_hash='b'*64)
        self.assertFalse(self.run.attempts.exists())

    def test_result_failure_cannot_erase_the_reserved_send(self):
        attempt=self.admit();mark_sent(attempt.pk)
        with patch.object(ChatEvaluationRun,'save',side_effect=DatabaseError('PRIVATE')):
            with self.assertRaises(DatabaseError): record_result(self.run.pk,'E01:extractive',{'reply':'public'})
        self.run.refresh_from_db();self.assertEqual(self.run.sent_count,1)
        self.assertEqual(self.run.committed_usd,attempt.reserved_usd)
        from chatbot.evaluation.runner import snapshot_run
        self.assertIn('E01:extractive', snapshot_run(self.run.pk)['attempts_without_result'])

    def test_incomplete_response_known_usage_is_counted_but_not_completed(self):
        attempt=self.admit();mark_sent(attempt.pk)
        settle(attempt.pk,tariff=self.tariff,usage=usage(),model=self.tariff.model,completed=False,diagnostic='incomplete_response')
        self.run.refresh_from_db();self.assertEqual(self.run.sent_count,1);self.assertEqual(self.run.completed_count,0)
        self.assertEqual(self.run.committed_usd,self.tariff.cost(100,10))


@override_settings(CHATBOT_PROVIDER='openai', CHATBOT_ENABLED=True, OPENAI_API_KEY='offline-only',
                   CHATBOT_OPENAI_MODEL='gpt-6-luna',CHATBOT_MAX_OUTPUT_TOKENS=400,CHATBOT_REQUEST_TIMEOUT=30,
                   CHATBOT_EVAL_PROJECT_REF='proj_synthetic')
class BudgetedSDKTests(TestCase):
    def setUp(self):
        self.directory=TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.price=Path(self.directory.name)/'price.json';self.price.write_text(json.dumps(price_data()))
        self.override=override_settings(CHATBOT_EVAL_PRICING_FILE=str(self.price));self.override.enable();self.addCleanup(self.override.disable)
        self.tariff=load_tariff(self.price,'gpt-6-luna');self.run=open_run(uuid4(),config(self.tariff))
        self.factory=patch('chatbot.providers.AsyncOpenAI').start();self.addCleanup(patch.stopall)
        self.sdk=self.factory.return_value.__aenter__.return_value
        self.create=AsyncMock();self.sdk.responses.create=self.create
        self.access=patch('chatbot.evaluation.provider.require_evaluation_access').start()
        self.public_access=patch('chatbot.providers.require_openai_access').start()
        self.create.return_value=response(selection_json()).model_copy(update={'usage':None})

    def provider(self,**kw):
        return BudgetedProvider(run_id=self.run.pk,case_id='E01',mode='extractive',tariff=self.tariff,
                                max_input_tokens=kw.get('max_input_tokens',1050000),max_output_tokens=kw.get('max_output_tokens',400))

    def ask(self,provider=None):
        return (provider or self.provider()).respond([ChatTurn('user','synthetic question')],language='es',knowledge=knowledge_fixture())

    def sdk_response(self,status='completed',known=True):
        from openai.types.responses.response_usage import ResponseUsage
        return response(selection_json(),status).model_copy(update={'usage':ResponseUsage.model_validate(usage()) if known else None})

    def test_sdk_is_created_only_after_durable_reservation_and_send_marker(self):
        events=[]
        def marked(identifier):
            mark_sent(identifier)
            events.append('durably_marked')
        def construct(**kwargs):
            self.assertEqual(events[-1], 'durably_marked')
            self.assertEqual(kwargs['max_retries'], 0)
            self.assertEqual(kwargs['project'], 'proj_synthetic')
            events.append('client_created')
            return self.factory.return_value
        self.factory.side_effect = construct
        with patch('chatbot.evaluation.provider.mark_sent', side_effect=marked) as marker:
            self.create.return_value=self.sdk_response()
            self.ask()
            marker.assert_called_once()
        self.assertEqual(events, ['durably_marked', 'client_created'])
        self.factory.assert_called_once()
        self.assertEqual(self.factory.call_args.kwargs['max_retries'],0)
        self.assertEqual(self.create.call_args.kwargs['service_tier'],'default')
        self.assertEqual(self.create.call_args.kwargs['truncation'],'disabled')
        self.run.refresh_from_db();self.assertEqual(self.run.sent_count,1)
        self.assertEqual(self.run.committed_usd,self.tariff.cost(100,10))

    def test_timeout_429_500_without_usage_hold_full_budget_no_retry(self):
        request=httpx.Request('POST','https://api.openai.com/v1/responses')
        errors=[APITimeoutError(request=request),RateLimitError('PRIVATE',response=httpx.Response(429,request=request),body={}),
                APIStatusError('PRIVATE',response=httpx.Response(500,request=request),body={})]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.run=open_run(uuid4(),config(self.tariff));self.create.side_effect=error
                with self.assertRaises(Exception):self.ask()
                attempt=self.run.attempts.get();self.run.refresh_from_db()
                self.assertEqual(attempt.state,'unknown');self.assertEqual(self.run.committed_usd,attempt.reserved_usd)
                self.assertEqual(self.run.sent_count,1)
        self.assertEqual(self.create.await_count,3)

    def test_incomplete_response_and_known_failure_usage_are_settled_without_retry(self):
        self.create.return_value=self.sdk_response('incomplete')
        with self.assertRaisesRegex(Exception,'incomplete_response'):self.ask()
        self.run.refresh_from_db();self.assertEqual(self.run.committed_usd,self.tariff.cost(100,10))
        self.assertEqual(self.run.completed_count,0)
        self.create.assert_awaited_once()

    def test_failed_429_with_reliable_usage_accounts_for_attempt_not_success(self):
        request=httpx.Request('POST','https://api.openai.com/v1/responses')
        self.create.side_effect=RateLimitError('PRIVATE-SECRET',response=httpx.Response(429,request=request),
                                             body={'usage':usage(),'model':'gpt-6-luna'})
        with self.assertRaisesRegex(Exception,'provider_rate_limit'):self.ask()
        self.run.refresh_from_db()
        self.assertEqual(self.run.committed_usd,self.tariff.cost(100,10))
        self.assertEqual((self.run.sent_count,self.run.completed_count),(1,0))
        self.assertNotIn('PRIVATE',str(self.run.results))
        self.create.assert_awaited_once()

    def test_missing_usage_is_not_treated_as_zero_cost(self):
        self.create.return_value=self.sdk_response(known=False);self.ask()
        attempt=self.run.attempts.get();self.run.refresh_from_db()
        self.assertEqual(self.run.committed_usd,attempt.reserved_usd)
        self.assertEqual(attempt.state,'unknown')

    def test_insufficient_token_ceiling_does_not_construct_client(self):
        with self.assertRaises(EvaluationStopped):self.ask(self.provider(max_input_tokens=10000))
        self.factory.assert_not_called();self.assertFalse(self.run.attempts.exists())
        with patch('chatbot.evaluation.provider.supported_usage_schema', return_value=False):
            with self.assertRaisesRegex(EvaluationStopped, 'unsupported_usage_schema'):self.ask()
        self.factory.assert_not_called()

    def test_output_above_cap_or_context_limit_never_constructs_client(self):
        with self.assertRaises(EvaluationStopped):self.ask(self.provider(max_output_tokens=401))
        knowledge=replace(knowledge_fixture(),context='x'*6001)
        with self.assertRaises(EvaluationStopped):
            self.provider().respond([ChatTurn('user','q')],language='es',knowledge=knowledge)
        self.factory.assert_not_called()

    def test_price_changed_or_removed_between_questions_fails_closed(self):
        self.price.write_text(json.dumps({**price_data(),'output_usd_per_million':'0.60'}))
        with self.assertRaisesRegex(EvaluationStopped,'pricing_changed'):self.ask()
        self.factory.assert_not_called()

    def test_settlement_database_failure_retains_reservation_for_resume(self):
        self.create.return_value=self.sdk_response()
        with patch('chatbot.evaluation.provider.settle',side_effect=DatabaseError('PRIVATE')):
            with self.assertRaises(DatabaseError):self.ask()
        attempt=self.run.attempts.get();self.run.refresh_from_db()
        self.assertEqual(attempt.state,'sent');self.assertEqual(self.run.committed_usd,attempt.reserved_usd)
        recovered=open_run(self.run.pk,self.run.configuration,resume=True)
        self.assertEqual(recovered.sent_count,1)
        with self.assertRaises(EvaluationStopped):self.ask()
        self.create.assert_awaited_once()


class EvaluationCommandTests(TestCase):
    def test_default_and_explicit_dry_run_never_construct_a_client_or_budget(self):
        for flags in ({},{'allow_live':True,'dry_run':True}):
            with patch('chatbot.providers.AsyncOpenAI') as sdk:
                output=StringIO();call_command('evaluate_chatbot_live',stdout=output,**flags)
                data=json.loads(output.getvalue());self.assertTrue(data['dry_run'])
                self.assertEqual(data['real_openai_calls'],0);sdk.assert_not_called()
        self.assertFalse(ChatEvaluationRun.objects.exists())

    def test_live_flag_cannot_bypass_ci_even_with_prices_key_and_activation(self):
        with TemporaryDirectory() as directory:
            price=Path(directory)/'price.json';price.write_text(json.dumps(price_data()))
            key=uuid4()
            with override_settings(CHATBOT_PROVIDER='openai',OPENAI_API_KEY='offline-key',CHATBOT_EVAL_ALLOW_LIVE=True,
                CHATBOT_EVAL_ENVIRONMENT='staging',CHATBOT_EVAL_PROJECT_REF='proj_synthetic',
                CHATBOT_EVAL_AUTHORIZATION_ID=str(key),CHATBOT_EVAL_PRICING_FILE=str(price)), \
                patch('chatbot.providers.AsyncOpenAI') as sdk:
                with self.assertRaises(CommandError):call_command('evaluate_chatbot_live',allow_live=True,run_id=str(key),stdout=StringIO())
                sdk.assert_not_called()
            self.assertFalse(ChatEvaluationRun.objects.exists())

    def test_cli_caps_and_dataset_allowlist_cannot_be_overridden(self):
        for kw in ({'max_requests':21},{'max_budget_usd':'1.001'},{'max_output_tokens':401}):
            with self.subTest(kw=kw),patch('chatbot.providers.AsyncOpenAI') as sdk,self.assertRaises(CommandError):
                call_command('evaluate_chatbot_live',stdout=StringIO(),**kw)
            sdk.assert_not_called()
        with self.assertRaises(EvaluationStopped):load_dataset('/tmp/arbitrary.json')

    def test_atomic_private_export_failure_does_not_destroy_previous_report(self):
        with TemporaryDirectory() as directory:
            target=Path(directory)/'report.json';atomic_json(target,{'before':True})
            with patch('chatbot.evaluation.output.os.replace',side_effect=OSError):
                with self.assertRaises(OSError):atomic_json(target,{'after':True})
            self.assertEqual(json.loads(target.read_text()),{'before':True})
            self.assertEqual(target.stat().st_mode & 0o777,0o600)


@skipUnless(connection.vendor=='postgresql','Requires PostgreSQL row/advisory locks.')
class EvaluationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tariff=validate_tariff(price_data(),'gpt-6-luna');self.run=open_run(uuid4(),config(self.tariff))

    def test_only_one_process_session_can_lock_same_authorization(self):
        with execution_lock(self.run.pk):
            with self.assertRaisesRegex(EvaluationStopped,'execution_busy'):
                with execution_lock(self.run.pk):pass
        with execution_lock(self.run.pk):pass

    def test_concurrent_admissions_cannot_exceed_one_dollar_or_duplicate_case(self):
        barrier=Barrier(12)
        def run(i):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    reserve(self.run.pk,f'E{i+1:02d}','extractive',tariff=self.tariff,input_ceiling=1050000,output_ceiling=400,
                            payload_hash='a'*64,source_hash='b'*64)
                    return True
                except EvaluationStopped:return False
            finally:connection.close()
        with ThreadPoolExecutor(max_workers=12) as pool:results=list(pool.map(run,range(12)))
        self.run.refresh_from_db();self.assertEqual(sum(results),1)
        self.assertLessEqual(self.run.committed_usd,Decimal(1));self.assertLessEqual(self.run.sent_count,20)

    def test_reservation_and_provider_boundary_have_no_open_transaction(self):
        self.assertFalse(connection.in_atomic_block)
        attempt=reserve(self.run.pk,'E01','extractive',tariff=self.tariff,input_ceiling=1050000,output_ceiling=400,
                        payload_hash='a'*64,source_hash='b'*64)
        mark_sent(attempt.pk)
        self.assertFalse(connection.in_atomic_block)

@skipUnless(connection.vendor=='postgresql','Positive live-path tests require isolated PostgreSQL; SDK is always mocked.')
@override_settings(CHATBOT_PROVIDER='openai', CHATBOT_ENABLED=True, OPENAI_API_KEY='offline-only',
                   CHATBOT_EVAL_PROJECT_REF='proj_synthetic',CHATBOT_MAX_OUTPUT_TOKENS=400,CHATBOT_REQUEST_TIMEOUT=30)
class EvaluationRunnerMockTests(TransactionTestCase):
    def test_comparison_pipeline_and_resume_use_one_persistent_budget_with_mocked_sdk(self):
        from chatbot.evaluation.fixtures import install_case
        cases,_=load_dataset('phase2-v1');install_case(cases[29])
        from locations.models import RecyclingCenter
        RecyclingCenter.objects.create(name='Punto Limpio de Adeje',address='Dirección sintética',latitude=28,
            longitude=-16,schedule='Lunes 09:00 a 14:00',is_active=True,show_on_public_map=True)
        with TemporaryDirectory() as directory:
            price=Path(directory)/'price.json';price.write_text(json.dumps(price_data()))
            run_id=uuid4()
            from openai.types.responses.response_usage import ResponseUsage
            async def respond(**args):
                self.assertFalse(connection.in_atomic_block)
                sources=json.loads(args['input'][1]['content'].partition('\n')[2])['sources']
                item=sources[0]
                if args['text']['format']['name']=='trueca_grounded_answer':
                    payload={'answer':item['content'],'sufficient':True,'claims':[
                        {'text':item['content'],'evidence':[{'source_id':item['source_id'],'quote':item['content']}]}]}
                else:payload={'selections':[{'source_id':item['source_id'],'content':item['content']}]}
                return response(json.dumps(payload)).model_copy(update={'usage':ResponseUsage.model_validate(usage())})
            with override_settings(CHATBOT_EVAL_PRICING_FILE=str(price)), \
                 patch('chatbot.evaluation.runner.require_evaluation_access'), \
                 patch('chatbot.evaluation.provider.require_evaluation_access'), \
                 patch('chatbot.providers.require_openai_access'),patch('chatbot.providers.AsyncOpenAI') as factory:
                create=AsyncMock(side_effect=respond);factory.return_value.__aenter__.return_value.responses.create=create
                first=evaluate(options(allow_live=True,run_id=str(run_id)))
                sent=first['send_intents']
                self.assertGreater(sent,0);self.assertLessEqual(sent,20)
                self.assertLessEqual(Decimal(first['committed_usd']),Decimal(1))
                self.assertEqual(first['stop_reason'],'completed')
                self.assertEqual(len(first['results']),20)
                self.assertFalse(first['results']['E02:extractive']['provider_attempted'])
                self.assertFalse(first['results']['E17:grounded']['provider_attempted'])
                second=evaluate(options(allow_live=True,run_id=str(run_id),resume=True))
                self.assertEqual(second['committed_usd'],first['committed_usd'])
                self.assertEqual(second['send_intents'],sent)
                self.assertEqual(create.await_count,sent)
                with self.assertRaisesRegex(EvaluationStopped,'execution_exists'):
                    evaluate(options(allow_live=True,run_id=str(run_id)))
                self.assertEqual(create.await_count,sent)

    def test_twenty_four_simultaneous_cheap_admissions_stop_at_twenty(self):
        tariff=validate_tariff({**price_data(),'input_usd_per_million':'.001',
                               'output_usd_per_million':'.001','special_input_usd_per_million':'.001'},'gpt-6-luna')
        run=open_run(uuid4(),config(tariff));barrier=Barrier(24)
        def admit(i):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                try:
                    reserve(run.pk,f'E{i+1:02d}','extractive',tariff=tariff,input_ceiling=1050000,
                            output_ceiling=400,payload_hash='a'*64,source_hash='b'*64)
                    return True
                except EvaluationStopped:return False
            finally:connection.close()
        with ThreadPoolExecutor(max_workers=24) as pool:results=list(pool.map(admit,range(24)))
        run.refresh_from_db();self.assertEqual(sum(results),20);self.assertEqual(run.admitted_count,20)
        self.assertLessEqual(run.committed_usd,Decimal(1))

@skipUnless(connection.vendor=='postgresql','Requires actual PostgreSQL durability settings.')
class EvaluationDurabilityTests(TransactionTestCase):
    def test_live_authorization_checks_database_durability_without_network(self):
        from chatbot.evaluation.access import require_evaluation_access
        key=uuid4()
        with override_settings(CHATBOT_ENABLED=True,CHATBOT_PROVIDER='openai',OPENAI_API_KEY='offline-only',
            CHATBOT_EVAL_ALLOW_LIVE=True,CHATBOT_EVAL_ENVIRONMENT='staging',
            CHATBOT_EVAL_PROJECT_REF='proj_synthetic',CHATBOT_EVAL_AUTHORIZATION_ID=str(key)), \
            patch('chatbot.safety.is_test_process',return_value=False), \
            patch('sys.argv',['manage.py','evaluate_chatbot_live','--allow-live']), \
            patch('chatbot.providers.AsyncOpenAI') as sdk:
            require_evaluation_access(key)
            with patch('chatbot.evaluation.access.connection.cursor') as cursor:
                cursor.return_value.__enter__.return_value.fetchone.side_effect = [('off',), ('on',)]
                with self.assertRaisesRegex(EvaluationStopped, 'durable_database_required'):
                    require_evaluation_access(key)
            sdk.assert_not_called()
