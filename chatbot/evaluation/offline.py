"""Reproducible mocked quality and local latency measurements, never semantic model scores."""
from dataclasses import asdict
from math import ceil
import re
import statistics
from time import perf_counter
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from chatbot.answering import ConversationAnswerer, restrict, private_question
from chatbot.conversation_context import ConversationContextResolver, split_questions
from chatbot.knowledge.conflicts import unsafe_content
from chatbot.grounding import response_format
from chatbot.knowledge import KnowledgeService
from chatbot.knowledge.normalization import fold
from chatbot.providers import SimulatedProvider, request_arguments, ChatTurn
from chatbot.services import Owner, get_session, send_message, validate_content
from chatbot.references import present_sources
from chatbot.models import ChatMessage
from uuid import uuid4
from .dataset import load_dataset
from .fixtures import install_case
from .pricing import digest


class RecordedMock(SimulatedProvider):
    def __init__(self):
        self.calls = []

    def respond(self, history, *, language, knowledge=None):
        args = request_arguments(history, language=language, knowledge=knowledge)
        self.calls.append({'mode': knowledge.generation_mode, 'context_chars': len(knowledge.context),
                           'schema_bytes': len(str(response_format(knowledge)).encode()),
                           'payload_utf8_bytes': len(str(args).encode()), 'tokens': None})
        return super().respond(history, language=language, knowledge=knowledge)


def source_metrics(expected, actual):
    expected, actual = set(expected), set(actual)
    true = len(expected & actual)
    return {'precision_at_k': true / len(actual) if actual else (1 if not expected else 0),
            'recall_at_k': true / len(expected) if expected else None,
            'expected': sorted(expected), 'selected': sorted(actual), 'k': len(actual)}


def evaluate_dataset(name='phase2-v1'):
    cases, signature = load_dataset(name)
    rows = []
    for case in cases:
        mapping = install_case(case)
        raw, selected_sources = [], []
        if not unsafe_content(case['question']) and not private_question(case['question']):
            for part in split_questions(case['question']):
                resolver = ConversationContextResolver().resolve(part, case['history'])
                if resolver.clarification:
                    continue
                bundle = KnowledgeService().retrieve(resolver.search_query, language=case['language'])
                filtered = restrict(bundle, resolver)
                raw.extend(f.source.source_id for f in bundle.fragments)
                selected_sources.extend(f.source.source_id for f in filtered.fragments)
        retrieval = source_metrics(mapping.values(), raw)
        selection = source_metrics(mapping.values(), selected_sources)
        for mode in ('extractive', 'grounded'):
            provider = RecordedMock()
            with override_settings(CHATBOT_GENERATION_MODE=mode):
                started = perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    reply = ConversationAnswerer().answer(case['question'], case['history'], language=case['language'],
                                                          provider_factory=lambda: provider)
                elapsed = (perf_counter() - started) * 1000
            text = fold(reply.text)
            failures = []
            for group in case['required_conditions'] + case['required_negations']:
                if not re.search(fold(group), text):
                    failures.append('required_condition_or_negation_missing')
            if 'PRIVATE-' in reply.text:
                failures.append('private_marker')
            if 'reservation.pickup_window' in case['blocked_claims'] and re.search(r'\b(72|7|siete|seven)\s+(horas|dias|hours|days)', text):
                failures.append('blocked_deadline')
            if 'points.reservation_charge' in case['blocked_claims'] and re.search(r'\b50\s+(puntos|points)', text):
                failures.append('blocked_charge')
            if len(provider.calls) > 1:
                failures.append('extra_provider_call')
            behavior = case['expected_behavior']
            expected_local = behavior in ('deterministic', 'clarification', 'abstention', 'conflict')
            route_ok = ((reply.route == behavior) if behavior in ('deterministic', 'clarification', 'conflict') else
                        reply.outcome == 'no_evidence' if behavior == 'abstention' else
                        bool(provider.calls) and bool(reply.references))
            rows.append({'case_id': case['id'], 'category': case['category'], 'language': case['language'],
                'question': case['question'], 'configured_mode': mode, 'effective_modes': [c['mode'] for c in provider.calls],
                'expected_behavior': behavior, 'behavior_matches': route_ok, 'reply': reply.text,
                'references': [asdict(r) for r in reply.references], 'retrieval': retrieval, 'selection': selection,
                'reference_selection': source_metrics(mapping.values(), [r.source_id for r in reply.references]),
                'provider_calls': len(provider.calls), 'expected_calls_match': (not provider.calls) if expected_local else bool(provider.calls),
                'diagnostic_violations': sorted(set(failures)), 'semantic_scores': None,
                'measurement': 'local_database_and_mock_provider', 'elapsed_ms': round(elapsed, 3), 'query_count': len(queries),
                'payloads': provider.calls})
    summary = {}
    for mode in ('extractive', 'grounded'):
        selected = [r for r in rows if r['configured_mode'] == mode]
        recalls = [r['selection']['recall_at_k'] for r in selected if r['selection']['recall_at_k'] is not None]
        positives = [r for r in selected if r['selection']['expected']]
        negatives = [r for r in selected if not r['selection']['expected']]
        summary[mode] = {'cases': len(selected), 'behavior_matches': sum(r['behavior_matches'] for r in selected),
            'provider_calls': sum(r['provider_calls'] for r in selected),
            'diagnostic_violations': sum(bool(r['diagnostic_violations']) for r in selected),
            'positive_source_cases': len(positives), 'negative_source_cases': len(negatives),
            'mean_selection_precision': statistics.mean(r['selection']['precision_at_k'] for r in positives),
            'mean_selection_recall': statistics.mean(recalls) if recalls else None,
            'mean_raw_retrieval_precision': statistics.mean(r['retrieval']['precision_at_k'] for r in positives),
            'mean_raw_retrieval_recall': statistics.mean(r['retrieval']['recall_at_k'] for r in positives),
            'mean_reference_recall': statistics.mean(r['reference_selection']['recall_at_k'] for r in positives),
            'negative_source_exclusion_rate': statistics.mean(not r['selection']['selected'] for r in negatives),
            'semantic_scores': None}
    return {'dataset': name, 'dataset_hash': signature, 'backend': connection.vendor,
            'provider': 'mock', 'real_openai_calls': 0, 'summary': summary, 'rows': rows}


def distribution(values):
    ordered = sorted(values)
    return {'n': len(ordered), 'median': round(statistics.median(ordered), 3),
            'p95': round(ordered[ceil(len(ordered) * .95) - 1], 3), 'max': round(max(ordered), 3)}


def benchmark(samples=20):
    cases, _ = load_dataset('phase2-v1')
    by_id = {c['id']: c for c in cases}
    profiles = [('deterministic', 'E02', 'extractive'), ('clarification', 'E21', 'extractive'),
                ('no_evidence', 'E15', 'extractive'), ('conflict', 'E06', 'extractive'),
                ('extractive_mock', 'E30', 'extractive'), ('grounded_mock', 'E30', 'grounded')]
    results = {}
    for label, case_id, mode in profiles:
        case = by_id[case_id]
        install_case(case)
        timings = {key: [] for key in ('validation', 'resolution', 'retrieval_including_ranking', 'answer_and_revalidation',
                                      'reference_presentation', 'reservation', 'persistence', 'endpoint_total', 'revalidation', 'ranking_python', 'queries')}
        for _ in range(samples):
            def measure(key, function):
                start = perf_counter(); value = function(); timings[key].append((perf_counter() - start) * 1000); return value
            measure('validation', lambda: validate_content(case['question']))
            resolved = measure('resolution', lambda: ConversationContextResolver().resolve(case['question'], case['history']))
            from chatbot.knowledge.sources import faq as faq_source
            original_rank = faq_source.lexical_score
            rank_ms = [0.0]
            def timed_rank(*args, **kwargs):
                start = perf_counter()
                try:
                    return original_rank(*args, **kwargs)
                finally:
                    rank_ms[0] += (perf_counter() - start) * 1000
            with patch.object(faq_source, 'lexical_score', side_effect=timed_rank):
                measure('retrieval_including_ranking', lambda: KnowledgeService().retrieve(resolved.search_query, language=case['language']))
            timings['ranking_python'].append(rank_ms[0])
            provider = RecordedMock()
            with override_settings(CHATBOT_GENERATION_MODE=mode):
                engine = ConversationAnswerer()
                retrieve = engine.knowledge.retrieve
                retrieval_number = [0]
                def timed_retrieve(*args, **kwargs):
                    retrieval_number[0] += 1
                    if retrieval_number[0] > 1:
                        return measure('revalidation', lambda: retrieve(*args, **kwargs))
                    return retrieve(*args, **kwargs)
                with patch.object(engine.knowledge, 'retrieve', side_effect=timed_retrieve):
                    answer = measure('answer_and_revalidation', lambda: engine.answer(case['question'], case['history'],
                                      language=case['language'], provider_factory=lambda: provider))
                # Actual public endpoint, including CSRF/session/JSON/view/persistence. Synthetic owners only.
                from django.test import Client
                client = Client()
                conversation = client.get('/chatbot/api/session/').json()['conversation']['id']
                from chatbot import services
                def instrument(key, original):
                    return lambda *a, **kw: measure(key, lambda: original(*a, **kw))
                with patch('chatbot.services.get_provider', return_value=RecordedMock()), \
                     patch('chatbot.services.reserve_message', side_effect=instrument('reservation', services.reserve_message)), \
                     patch('chatbot.services.finish_message', side_effect=instrument('persistence', services.finish_message)):
                    with CaptureQueriesContext(connection) as queries:
                        response = measure('endpoint_total', lambda: client.post('/chatbot/api/messages/', {
                            'conversation_id': conversation, 'request_id': str(uuid4()), 'content': case['question']}, content_type='application/json'))
                    if response.status_code != 200:
                        raise AssertionError('Synthetic endpoint failed')
                timings['queries'].append(len(queries))
                messages = list(ChatMessage.objects.filter(conversation_id=conversation))
                measure('reference_presentation', lambda: present_sources(messages))
        results[label] = {k: distribution(v) for k, v in timings.items() if v}
    return {'environment': f'ephemeral {connection.vendor}; mock provider; sequential; no network',
            'samples_per_profile': samples, 'profiles': results, 'ranking_note': 'Ranking included in retrieval; not isolated from SQL/extraction.',
            'real_openai_latency': None, 'real_billed_tokens': None}


def history_probe(samples=20):
    """Bounded synthetic scaling probe: 20/200/1000 messages, repeated sources, own owner."""
    from django.contrib.auth import get_user_model
    from faq.models import FAQ
    from chatbot.references import reference_payload
    from chatbot.grounding import ValidatedReference
    from chatbot.knowledge.sources.base import fragment
    from django.utils import timezone
    from hashlib import sha256
    faq = FAQ.objects.filter(is_published=True).first()
    result = {}
    for size in (20, 200, 1000):
        owner_user = get_user_model().objects.create_user(email=f'performance-history-{size}@example.invalid')
        owner = Owner(user_id=owner_user.pk)
        conversation = get_session(owner)['conversation']['id']
        item = fragment('faq', faq.pk, faq.question, faq.answer, url=f'/faq/#faq-question-{faq.pk}',
                        updated_at=faq.updated_at, now=timezone.now(), relevance=1)
        reference = ValidatedReference(item.source.source_id, item.source.public_url, item.content_hash,
            item.content, 'faq', item.title, item.source.source_version, (sha256(item.content.encode()).hexdigest(),))
        provenance = reference_payload((reference,))
        rows = []
        for _ in range(size // 2):
            key = uuid4()
            rows.append(ChatMessage(conversation_id=conversation, role='user', status='completed', request_id=key,
                                    content='Pregunta sintética de rendimiento'))
            rows.append(ChatMessage(conversation_id=conversation, role='assistant', status='completed', request_id=key,
                                    content='Respuesta sintética de rendimiento', sources=provenance))
        ChatMessage.objects.bulk_create(rows)
        elapsed, queries = [], []
        for _ in range(samples):
            start = perf_counter()
            with CaptureQueriesContext(connection) as captured:
                snapshot = get_session(owner)
            elapsed.append((perf_counter() - start) * 1000)
            queries.append(len(captured))
            if len(snapshot['messages']) != size:
                raise AssertionError('Synthetic history size mismatch')
        result[str(size)] = {'elapsed_ms': distribution(elapsed), 'query_count': distribution(queries)}
    return result
