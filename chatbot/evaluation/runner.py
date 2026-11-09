"""Sequential comparison; dry-run has no SDK client and live mode never seeds a corpus."""
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
import hashlib
import platform
import openai
import django

from django.conf import settings
from django.test import override_settings

from chatbot.answering import ConversationAnswerer
from chatbot.providers import SimulatedProvider, request_arguments
from chatbot.safety import ProviderError
from .access import limits, require_evaluation_access
from .dataset import load_dataset
from .errors import EvaluationStopped
from .ledger import execution_lock, open_run, record_result, stop
from .models import ChatEvaluationRun
from .pricing import digest, load_tariff
from .pricing import decimal
from .provider import BudgetedProvider
from .token_budget import BOUND_VERSION, input_bound
from .selection import select_cases, execution_pairs


class PreviewProvider(SimulatedProvider):
    def __init__(self, max_input_tokens):
        self.calls = []
        self.max_input_tokens = max_input_tokens

    def respond(self, history, *, language, knowledge=None):
        arguments = request_arguments(history, language=language, knowledge=knowledge)
        arguments.update(service_tier='default', truncation='disabled')
        try:
            bound, blocker = input_bound(arguments, max_input_tokens=self.max_input_tokens).report(), ''
        except EvaluationStopped as error:
            bound, blocker = None, error.code
        self.calls.append({'effective_mode': knowledge.generation_mode,
                           'sources': [(f.source.source_id, f.content_hash) for f in knowledge.fragments],
                           'context_chars': len(knowledge.context), 'input_bound': bound, 'bound_blocker': blocker})
        return super().respond(history, language=language, knowledge=knowledge)


def configuration(options, dataset_hash, cases, tariff):
    code = Path(__file__).resolve().parent.parent
    files = sorted(str(p.relative_to(code)) for p in code.rglob('*.py')
                   if not {'tests', 'migrations', '__pycache__'}.intersection(p.relative_to(code).parts))
    return {'dataset': options['dataset'], 'dataset_hash': dataset_hash, 'model': options['model'],
            'modes': ['extractive', 'grounded'] if options['mode'] == 'both' else [options['mode']],
            'cases': [c['id'] for c in cases], 'approved_case_ids': list(settings.CHATBOT_EVAL_CASE_IDS),
            'execution_order': [f'{c["id"]}:{mode}' for c, mode in execution_pairs(cases,
                ['extractive', 'grounded'] if options['mode'] == 'both' else [options['mode']],
                scoped=options.get('case_ids') is not None)], 'max_requests': options['max_requests'],
            'max_budget_usd': str(limits(options)), 'max_input_tokens': options['max_input_tokens'],
            'max_output_tokens': options['max_output_tokens'], 'timeout': min(settings.CHATBOT_REQUEST_TIMEOUT, 30),
            'tariff_hash': tariff.signature if tariff else None, 'project': settings.CHATBOT_EVAL_PROJECT_REF,
            'approved_tariff': ({'input_usd_per_million': str(tariff.input_rate),
                'output_usd_per_million': str(tariff.output_rate),
                'special_input_usd_per_million': str(tariff.special_input_rate),
                'fixed_request_usd': str(tariff.fixed_rate), 'margin': str(tariff.margin),
                'verified_on': tariff.verified_on, 'currency': 'USD',
                'source': 'https://developers.openai.com/api/docs/pricing',
                'model_source': 'https://developers.openai.com/api/docs/models/' + tariff.model,
                'input_ceiling': tariff.input_ceiling} if tariff else None),
            'runtime': [django.get_version(), openai.__version__, platform.python_version()],
            'history_messages': settings.CHATBOT_HISTORY_MESSAGES, 'context_turns': settings.CHATBOT_CONTEXT_TURNS,
            'reasoning_effort': settings.CHATBOT_REASONING_EFFORT, 'max_input_chars': settings.CHATBOT_MAX_INPUT_CHARS,
            'token_bound_method': settings.CHATBOT_EVAL_TOKEN_BOUND_METHOD, 'token_bound_version': BOUND_VERSION,
            'knowledge_limits': [settings.CHATBOT_KNOWLEDGE_MAX_FRAGMENTS, settings.CHATBOT_KNOWLEDGE_FRAGMENT_CHARS,
                                 settings.CHATBOT_KNOWLEDGE_CONTEXT_CHARS],
            'code_hash': digest({name: hashlib.sha256((code / name).read_bytes()).hexdigest() for name in files})}


def snapshot_run(run_id):
    run = ChatEvaluationRun.objects.get(pk=run_id)
    attempted = {f'{a.case_id}:{a.mode}' for a in run.attempts.all()}
    order = run.configuration.get('execution_order', [f'{case}:{mode}' for case in run.configuration['cases']
                                                      for mode in run.configuration['modes']])
    pending = [key for key in order if key not in run.results and key not in attempted]
    return {'run_id': str(run.pk), 'measurement': 'live_only_if_authorized', 'configuration': {k: v for k, v in run.configuration.items() if k != 'project'},
            'admitted': run.admitted_count, 'send_intents': run.sent_count, 'completed_provider_responses': run.completed_count,
            'committed_usd': str(run.committed_usd), 'remaining_usd': str(run.max_budget_usd - run.committed_usd),
            'peak_committed_usd': str(run.peak_committed_usd), 'billing_scope': 'local_admission_ceiling_not_provider_invoice',
            'stop_reason': run.stop_reason, 'pending': pending, 'results': run.results,
            'attempts_without_result': sorted(attempted.difference(run.results)),
            'attempts': [{**a, 'id': str(a['id']), 'reserved_usd': str(a['reserved_usd']), 'charged_usd': str(a['charged_usd'])}
                         for a in run.attempts.values('id', 'case_id', 'mode', 'state', 'reserved_usd', 'charged_usd',
                                                     'usage', 'diagnostic', 'input_ceiling', 'output_ceiling')]}


def evaluate(options):
    limits(options)
    cases, dataset_hash = load_dataset(options['dataset'])
    cases = select_cases(cases, options)
    dry = options.get('dry_run') or not options.get('allow_live')
    tariff, pricing_reason = None, ''
    try:
        tariff = load_tariff(settings.CHATBOT_EVAL_PRICING_FILE, options['model'], max_age_days=settings.CHATBOT_EVAL_PRICE_MAX_AGE_DAYS)
    except EvaluationStopped as error:
        pricing_reason = error.code
        if not dry:
            raise
    config = configuration(options, dataset_hash, cases, tariff)
    modes = config['modes']
    if dry:
        results = []
        preview_committed = decimal('0')
        preview_admitted = 0
        for case, mode in execution_pairs(cases, modes, scoped=options.get('case_ids') is not None):
            preview = PreviewProvider(options['max_input_tokens'])
            with override_settings(CHATBOT_GENERATION_MODE=mode, CHATBOT_OPENAI_MODEL=options['model'],
                                   CHATBOT_MAX_OUTPUT_TOKENS=options['max_output_tokens']):
                reply = ConversationAnswerer().answer(case['question'], case['history'], language=case['language'],
                                                     provider_factory=lambda: preview)
            cost = (tariff.cost(tariff.input_ceiling, options['max_output_tokens'], margin=True)
                    if tariff is not None and preview.calls else None)
            bound = preview.calls[0]['input_bound'] if preview.calls else None
            can_bound = tariff is not None and bound is not None
            admission = bool(preview.calls and can_bound and preview_admitted < options['max_requests']
                             and preview_committed + cost <= decimal(options['max_budget_usd']))
            if admission:
                preview_committed += cost
                preview_admitted += 1
            results.append({'case_id': case['id'], 'mode': mode, 'simulated': True,
                            'would_generate': bool(preview.calls), 'reply': reply.text,
                            'input_bound': bound,
                            'bound_blocker': preview.calls[0]['bound_blocker'] if preview.calls else '',
                'effective_mode': preview.calls[0]['effective_mode'] if preview.calls else None,
                'input_sources': preview.calls[0]['sources'] if preview.calls else [],
                            'maximum_reserve_usd': str(cost) if cost is not None else None,
                            'admissible_if_all_previous_usage_unknown': admission,
                            'route': reply.route, 'sources': [asdict(r) for r in reply.references]})
        return {'dry_run': True, 'real_openai_calls': 0, 'dry_run_committed_usd': '0', 'configuration': {k: v for k, v in config.items() if k != 'project'},
                'actual_run_budget': 'not_read_or_reset',
                'planned_questions': len(cases), 'planned_question_modes': len(cases) * len(modes),
                'maximum_possible_provider_calls': min(options['max_requests'], sum(r['would_generate'] for r in results)),
                'token_bound_method': 'documented_full_context_ceiling', 'adjusted_bound_available': False,
                'warnings': ['No local verified bound for Responses framing and Structured Outputs overhead.',
                             'Byte sizes are diagnostics, not tokens. No remote counting endpoint was invoked.',
                             'Preview admissibility is hypothetical; live access requires separate authorization.',
                             'Unknown usage stops live execution; this preview is not permission to continue after it.'],
                'pricing_admission_blocker': pricing_reason,
                'worst_case_preview_only': {'admitted': preview_admitted, 'committed_usd': str(preview_committed)},
                'results': results}
    if options['dataset'] != 'pilot-v1':
        raise EvaluationStopped('live_dataset_not_allowed')
    run_id = options.get('run_id')
    require_evaluation_access(run_id)
    if tariff.input_ceiling > options['max_input_tokens']:
        raise EvaluationStopped('unreliable_cost_bound')
    with execution_lock(run_id):
        run = open_run(run_id, config, resume=options.get('resume', False))
        halted = False
        for case, mode in execution_pairs(cases, modes, scoped=options.get('case_ids') is not None):
            key = f'{case["id"]}:{mode}'
            run.refresh_from_db()
            if key in run.results or run.attempts.filter(case_id=case['id'], mode=mode).exists():
                continue  # Includes unknown attempts whose result export was interrupted.
            provider = BudgetedProvider(run_id=run_id, case_id=case['id'], mode=mode, tariff=tariff,
                max_input_tokens=options['max_input_tokens'], max_output_tokens=options['max_output_tokens'])
            started = perf_counter()
            try:
                # Overrides are process-local to this management command, never public API settings.
                with override_settings(CHATBOT_GENERATION_MODE=mode, CHATBOT_OPENAI_MODEL=options['model'],
                                       CHATBOT_MAX_OUTPUT_TOKENS=options['max_output_tokens'],
                                       CHATBOT_REQUEST_TIMEOUT=min(settings.CHATBOT_REQUEST_TIMEOUT, 30)):
                    reply = ConversationAnswerer().answer(case['question'], case['history'], language=case['language'],
                                                         provider_factory=lambda: provider)
                result = {'question': case['question'], 'language': case['language'], 'mode': mode,
                          'reply': reply.text, 'route': reply.route, 'outcome': reply.outcome,
                          'sources': [asdict(r) for r in reply.references], 'error': None}
            except EvaluationStopped as error:
                stop(run_id, error.code)
                halted = True
                break
            except ProviderError as error:
                result = {'question': case['question'], 'language': case['language'], 'mode': mode,
                          'error': error.code, 'reply': None, 'sources': []}
            result['elapsed_ms'] = round((perf_counter() - started) * 1000, 3)
            result['provider_attempted'] = provider.attempt is not None
            result['effective_mode'] = provider.effective_mode
            result['input_bound'] = provider.bound.report() if hasattr(provider, 'bound') else None
            result['input_source_hash'] = (provider.attempt.source_hash if provider.attempt is not None else
                digest(sorted({(r['source_id'], r['content_hash'], r['public_url']) for r in result['sources']})))
            previous = [value for name, value in run.results.items() if name.startswith(case['id'] + ':')]
            result['comparison_sources_equal'] = (all(v.get('input_source_hash') == result['input_source_hash'] for v in previous)
                                                   if previous else None)
            if result['comparison_sources_equal'] is False:
                stop(run_id, 'source_versions_changed')
            # An export or result failure cannot erase the durable send/reservation.
            record_result(run_id, key, result)
            run.refresh_from_db()
            if run.stop_reason:
                halted = True
                break
        if not halted:
            run.refresh_from_db()
            missing = any(f'{a.case_id}:{a.mode}' not in run.results for a in run.attempts.all())
            stop(run_id, 'completed_with_missing_results' if missing else 'completed')
        return snapshot_run(run_id)
