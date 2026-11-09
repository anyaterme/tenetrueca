"""Offline fidelity fixtures: these validate controls, not real-model semantic quality."""
import json
from dataclasses import replace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.db import DatabaseError, connection
from django.test import TestCase, SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import translation
from django.utils import timezone

from faq.models import FAQ
from catalog.models import Category
from locations.models import RecyclingCenter
from chatbot.answering import ConversationAnswerer
from chatbot.generation import choose_mode, parse_grounded
from chatbot.grounding import EvidenceSelection, GeneratedAnswer, GroundedClaim, response_format
from chatbot.knowledge import KnowledgeService
from chatbot.models import ChatMessage, ChatSessionUsage
from chatbot.references import present_sources, current_sources
from chatbot.safety import ProviderError
from chatbot.tests.fixtures import knowledge_fixture
from chatbot.knowledge.sources.base import fragment


PROCEDURE = ('Inicia sesión y abre la opción de publicación. Completa los datos del objeto y añade fotografías. '
             'Envía la propuesta al equipo de TRUEC@ para su revisión. Consulta la ayuda para conocer el proceso.')
SUMMARY = 'Para publicar, inicia sesión, completa los datos y añade fotografías. Envía la propuesta para su revisión.'


def natural(knowledge, text=SUMMARY, ids=None):
    selected = tuple(EvidenceSelection(f.source.source_id, f.content) for f in knowledge.fragments
                     if ids is None or f.source.source_id in ids)
    return GeneratedAnswer(selected, text, (GroundedClaim(text, selected),))


@override_settings(CHATBOT_ENABLED=True, CHATBOT_GENERATION_MODE='grounded', CHATBOT_RATE_LIMIT_REQUESTS=1000)
class NaturalAnswerTests(TestCase):
    def setUp(self):
        self.faq = FAQ.objects.create(question='¿Cómo publicar un objeto?', answer=PROCEDURE)
        self.engine = ConversationAnswerer()
        self.provider = Mock()
        self.provider.respond.side_effect = lambda history, *, language, knowledge: natural(knowledge)
        self.factory = Mock(return_value=self.provider)

    def answer(self, question='Resume cómo publicar un objeto', language='es'):
        return self.engine.answer(question, language=language, provider_factory=self.factory)

    def test_long_procedure_is_reformulated_with_exact_evidence_and_one_call(self):
        answer = self.answer()
        self.assertEqual(answer.text, SUMMARY)
        self.assertNotEqual(answer.text, PROCEDURE)
        self.assertTrue(answer.references)
        self.assertEqual(answer.route, 'generation')
        self.assertEqual(self.provider.respond.call_args.kwargs['knowledge'].generation_mode, 'grounded')
        self.provider.respond.assert_called_once()

    def test_multiple_compatible_sources_are_synthesized_in_one_call(self):
        second = FAQ.objects.create(question='Publicar fotografías', answer='Añade fotografías claras del objeto para presentar la propuesta.')
        answer = self.answer()
        self.assertEqual(len(answer.references), 2)
        self.assertEqual({r.source_id for r in answer.references}, {f'faq:{self.faq.pk}', f'faq:{second.pk}'})
        self.provider.respond.assert_called_once()

    def test_active_english_allows_explanation_without_translating_public_title(self):
        self.provider.respond.side_effect = lambda history, *, language, knowledge: natural(
            knowledge, 'To publish an object, sign in, add photographs and submit the proposal for review.')
        answer = self.answer('Explain how to publish an object', 'en')
        self.assertIn('To publish an object', answer.text)
        self.assertEqual(answer.references[0].title, self.faq.question)
        self.assertEqual(self.provider.respond.call_args.kwargs['language'], 'en')

    def test_three_sentences_request_is_style_not_a_platform_numeric_rule(self):
        texts = ('Inicia sesión para publicar un objeto.', 'Añade fotografías del objeto.',
                 'Envía la propuesta para su revisión.')
        def three(history, *, language, knowledge):
            evidence = (EvidenceSelection(knowledge.fragments[0].source.source_id, PROCEDURE),)
            return GeneratedAnswer(evidence, '\n\n'.join(texts), tuple(GroundedClaim(t, evidence) for t in texts))
        self.provider.respond.side_effect = three
        answer = self.answer('Explica cómo publicar un objeto en tres frases')
        self.assertEqual(answer.text, '\n\n'.join(texts))
        self.assertEqual(self.provider.respond.call_args.kwargs['knowledge'].generation_mode, 'grounded')

    def test_long_faq_summary_uses_a_short_quote_without_copying_the_document(self):
        self.faq.answer = PROCEDURE + ' ' + ('Puedes consultar la ayuda de publicación para preparar las fotografías del objeto. ' * 4)
        self.faq.save()
        def summary(history, *, language, knowledge):
            citation = EvidenceSelection(knowledge.fragments[0].source.source_id, PROCEDURE)
            return GeneratedAnswer((citation,), SUMMARY, (GroundedClaim(SUMMARY, (citation,)),))
        self.provider.respond.side_effect = summary
        answer = self.answer()
        self.assertEqual(answer.text, SUMMARY)
        self.assertLess(len(answer.text), len(self.faq.answer) // 2)

    @override_settings(CHATBOT_GENERATION_MODE='extractive')
    def test_extractive_rollback_rejects_paraphrase_and_returns_whole_evidence(self):
        answer = self.answer()
        self.assertIn(PROCEDURE, answer.text)
        self.assertNotEqual(answer.text, SUMMARY)
        self.provider.respond.assert_called_once()

    def test_conditions_exceptions_prohibitions_negations_and_numbers_remain_strict(self):
        units = ('No publiques documentos personales.', 'Publica solo si el objeto cumple las condiciones.',
                 'Se permite publicar, excepto los artículos prohibidos.', 'Puedes publicar hasta 2 objetos.',
                 'You must keep the original receipt.', 'Never deliver excluded objects.',
                 'Publishing is allowed only if the conditions are met.')
        for content in units:
            with self.subTest(content=content):
                self.faq.answer = content
                self.faq.save()
                bundle = KnowledgeService().retrieve('publicar objeto publishing objects')
                self.assertEqual(choose_mode('Explica la publicación', bundle), 'extractive')

    def test_mutated_normative_response_is_not_shown_and_preserves_negation(self):
        self.faq.answer = 'No publiques documentos personales.'
        self.faq.save()
        self.provider.respond.side_effect = lambda history, *, language, knowledge: natural(knowledge, 'Publica documentos personales.')
        answer = self.answer()
        self.assertIn('No publiques', answer.text)
        self.assertNotEqual(answer.text, 'Publica documentos personales.')
        self.provider.respond.assert_called_once()

    def test_generated_critical_or_private_additions_fall_back_without_retry(self):
        texts = ('Recibes 999 puntos al publicar.', 'Tu objeto será aprobado.', 'El centro está abierto ahora.',
                 'No publiques objetos.', 'El plazo es de siete días.', 'Escribe a private@example.invalid.',
                 '<img src=x onerror=alert(1)>', 'Consulta https://evil.invalid para publicar.',
                 'La luna tiene bicicletas de chocolate.')
        for text in texts:
            with self.subTest(text=text):
                self.provider.respond.side_effect = lambda history, *, language, knowledge, t=text: natural(knowledge, t)
                answer = self.answer()
                self.assertIn(PROCEDURE, answer.text)
                self.assertNotEqual(answer.text, text)
        self.assertEqual(self.provider.respond.call_count, len(texts))

    def test_invented_id_quote_and_unattributed_text_fall_back(self):
        for mode in ('id', 'quote', 'text'):
            def malicious(history, *, language, knowledge):
                result = natural(knowledge)
                if mode == 'text':
                    return replace(result, text=result.text + '\nOtra afirmación.')
                evidence = EvidenceSelection('faq:999999' if mode == 'id' else knowledge.fragments[0].source.source_id,
                                             PROCEDURE if mode == 'id' else 'Cita inventada')
                return replace(result, claims=(GroundedClaim(SUMMARY, (evidence,)),))
            with self.subTest(mode=mode):
                self.provider.respond.side_effect = malicious
                self.assertIn(PROCEDURE, self.answer().text)

    def test_no_references_is_rejected_even_for_plausible_text(self):
        self.provider.respond.side_effect = None
        self.provider.respond.return_value = GeneratedAnswer((), SUMMARY, (GroundedClaim(SUMMARY, ()),))
        self.assertIn(PROCEDURE, self.answer().text)

    def test_insufficient_evidence_is_local_or_structured_abstention(self):
        answer = self.answer('¿Existen dinosaurios espaciales?')
        self.assertEqual(answer.outcome, 'no_evidence')
        self.factory.assert_not_called()
        self.provider.respond.side_effect = None
        self.provider.respond.return_value = GeneratedAnswer((), '', (), False)
        self.assertEqual(self.answer().outcome, 'no_evidence')
        self.provider.respond.assert_called_once()

    def test_blocked_claims_and_registration_typo_make_zero_provider_calls(self):
        for question in ('¿Cuánto tiempo tengo para recoger?', '¿Cuándo se descuentan los 50 puntos?',
                         '¿Puedo entregar una cafetera?', 'Me dan puntos por registrame?'):
            answer = self.answer(question)
            self.assertIn(answer.route, ('deterministic', 'conflict'))
        self.assertIn('100 puntos', answer.text)
        self.factory.assert_not_called()

    def test_unrelated_charge_conflict_does_not_contaminate_registration(self):
        FAQ.objects.create(question='Puntos al reservar', answer='Se cobran 50 puntos al reservar.')
        answer = self.answer('Me dan puntos por registrame?')
        self.assertEqual(answer.route, 'deterministic')
        self.assertIn('activas tu cuenta', answer.text)
        self.assertNotIn('confirmada', answer.text)
        self.factory.assert_not_called()

    def test_source_changes_while_waiting_invalidates_the_generated_answer(self):
        def change(history, *, language, knowledge):
            self.faq.answer = 'Nueva explicación pública.'
            self.faq.save()
            return natural(knowledge)
        self.provider.respond.side_effect = change
        result = self.answer()
        self.assertEqual(result.outcome, 'no_evidence')
        self.assertFalse(result.references)
        self.assertNotIn(SUMMARY, result.text)

    def test_technical_timeout_is_not_a_counted_validation_fallback(self):
        self.provider.respond.side_effect = ProviderError('timeout')
        with self.assertRaisesRegex(ProviderError, 'timeout'):
            self.answer()
        self.provider.respond.assert_called_once()


class GroundedSchemaTests(SimpleTestCase):
    def setUp(self):
        self.bundle = replace(knowledge_fixture(PROCEDURE), generation_mode='grounded')

    def test_schema_has_no_arbitrary_links_or_duplicate_full_evidence(self):
        schema = response_format(self.bundle)
        self.assertTrue(schema['strict'])
        self.assertEqual(set(schema['schema']['required']), {'answer', 'claims', 'sufficient'})
        self.assertNotIn(PROCEDURE, json.dumps(schema))
        self.assertNotIn('url', schema['schema']['properties'])

    def test_valid_reformulation_with_exact_quote_parses(self):
        result = parse_grounded(json.dumps({'answer': SUMMARY, 'sufficient': True, 'claims': [
            {'text': SUMMARY, 'evidence': [{'source_id': 'faq:1', 'quote': PROCEDURE}]}]}), self.bundle)
        self.assertEqual(result.text, SUMMARY)
        self.assertEqual(result.selections[0].content, PROCEDURE)

    def test_invalid_structures_are_rejected(self):
        for data in ('not JSON', '[]', '{}', '{"answer":null,"claims":[],"sufficient":true}',
                     '{"answer":"Invented","claims":[],"sufficient":true}',
                     '{"answer":"","claims":[],"sufficient":false,"url":"/private/"}'):
            with self.subTest(data=data), self.assertRaisesRegex(ProviderError, 'invalid_grounding'):
                parse_grounded(data, self.bundle)


@override_settings(CHATBOT_ENABLED=True, CHATBOT_GENERATION_MODE='grounded', CHATBOT_RATE_LIMIT_REQUESTS=1000)
class ReferencePersistenceTests(TestCase):
    def setUp(self):
        self.faq = FAQ.objects.create(question='¿Cómo publicar un objeto?', answer=PROCEDURE)
        self.conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']
        self.provider = Mock(respond=Mock(side_effect=lambda history, *, language, knowledge: natural(knowledge)))
        self.factory = patch('chatbot.services.get_provider', return_value=self.provider)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def send(self, key=None, **extra):
        return self.client.post('/chatbot/api/messages/', {'conversation_id': self.conversation,
            'request_id': str(key or uuid4()), 'content': 'Resume cómo publicar un objeto', **extra}, content_type='application/json')

    def assistant(self, response):
        return response.json()['messages'][-1]

    def test_sources_are_persisted_atomically_and_restored_without_model_calls(self):
        response = self.send()
        self.assertEqual(response.status_code, 200)
        message = ChatMessage.objects.get(role='assistant')
        entry = message.sources[0]
        self.assertEqual(entry['source_id'], f'faq:{self.faq.pk}')
        self.assertEqual(len(entry['evidence_hashes'][0]), 64)
        self.assertIn('version', entry)
        self.assertNotIn(PROCEDURE, json.dumps(entry))
        self.assertNotIn('internal_locator', json.dumps(entry))
        restored = self.client.get('/chatbot/api/session/')
        self.assertEqual(self.assistant(restored), self.assistant(response))
        self.assertNotIn('source_id', str(self.assistant(restored)['sources']))
        self.provider.respond.assert_called_once()

    def test_reset_and_previous_owned_conversation_preserve_references(self):
        first = self.assistant(self.send())
        self.client.post('/chatbot/api/reset/', {}, content_type='application/json')
        old = self.client.get('/chatbot/api/session/', {'conversation_id': self.conversation})
        self.assertEqual(first, self.assistant(old))
        self.assertEqual(old.json()['usage']['consumed'], 1)

    def test_duplicate_does_not_store_twice_or_charge_twice(self):
        key = uuid4()
        first = self.send(key)
        second = self.send(key)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(ChatMessage.objects.filter(role='assistant').count(), 1)
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 1)
        self.provider.respond.assert_called_once()

    def test_legacy_messages_have_no_sources(self):
        ChatMessage.objects.create(conversation_id=self.conversation, role='assistant', status='completed',
                                   request_id=uuid4(), content='Mensaje histórico')
        self.assertEqual(self.assistant(self.client.get('/chatbot/api/session/'))['sources'], [])

    def test_edited_hidden_deleted_sources_lose_all_public_metadata_without_rewriting_text(self):
        for action in ('edit', 'hide', 'delete'):
            with self.subTest(action=action):
                self.faq = FAQ.objects.create(question='Publicar objeto', answer=PROCEDURE)
                self.send()
                if action == 'edit':
                    self.faq.question = 'NUEVO TÍTULO NO ATRIBUIBLE'
                    self.faq.save()
                elif action == 'hide':
                    self.faq.is_published = False
                    self.faq.save()
                else:
                    self.faq.delete()
                # Find the message associated with this public FAQ, not an older source.
                message = ChatMessage.objects.filter(role='assistant').last()
                response = self.client.get('/chatbot/api/session/')
                public = next(m for m in response.json()['messages'] if m['id'] == message.pk)
                relevant = [s for s in public['sources'] if not s.get('available')]
                self.assertTrue(relevant)
                self.assertEqual(relevant[0], {'available': False})
                self.assertEqual(public['content'], message.content)

    def test_invalid_stored_urls_are_never_presented(self):
        self.send()
        message = ChatMessage.objects.get(role='assistant')
        for url in ('javascript:alert(1)', 'data:text/html,evil', '//evil.invalid/', '/admin/', '/faq/#faq-question-99999'):
            message.sources[0]['url'] = url
            message.save(update_fields=['sources'])
            self.assertEqual(self.assistant(self.client.get('/chatbot/api/session/'))['sources'], [{'available': False}])

    def test_client_sources_are_ignored(self):
        result = self.send(sources=[{'title': 'FORGED', 'url': 'javascript:evil', 'type': 'faq'}])
        self.assertNotIn('FORGED', str(result.json()))
        self.assertNotIn('javascript:', str(result.json()))

    def test_persistence_failure_releases_quota_and_rolls_back_sources(self):
        original = ChatMessage.save
        def fail(message, *args, **kwargs):
            if message.role == 'assistant':
                raise DatabaseError('PRIVATE FAILURE')
            return original(message, *args, **kwargs)
        key = uuid4()
        with patch.object(ChatMessage, 'save', autospec=True, side_effect=fail):
            self.assertEqual(self.send(key).status_code, 503)
        self.assertFalse(ChatMessage.objects.filter(role='assistant').exists())
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (0, 0))
        self.assertEqual(self.send(key).status_code, 502)
        self.provider.respond.assert_called_once()

    def test_historical_revalidation_is_batched_for_many_messages(self):
        self.send()
        original = ChatMessage.objects.get(role='assistant')
        for _ in range(24):
            ChatMessage.objects.create(conversation=original.conversation, role='assistant', status='completed',
                                       request_id=uuid4(), content=SUMMARY, sources=original.sources)
        rows = list(ChatMessage.objects.filter(role='assistant'))
        with CaptureQueriesContext(connection) as queries:
            sources = present_sources(rows)
        self.assertEqual(len(queries), 1)
        self.assertTrue(all(value[0]['available'] for value in sources.values()))

    def test_historical_distinct_sources_are_bounded_to_one_hundred_in_one_faq_query(self):
        documents = FAQ.objects.bulk_create([FAQ(question=f'Documento público {i}', answer=f'Información pública única {i}.') for i in range(105)])
        stored = []
        for faq in documents:
            item = fragment('faq', faq.pk, faq.question, faq.answer, url=f'/faq/#faq-question-{faq.pk}',
                            updated_at=None, now=timezone.now(), relevance=1)
            stored.append({'source_id': item.source.source_id, 'type': 'faq', 'title': item.title,
                           'url': item.source.public_url, 'content_hash': item.content_hash,
                           'version': item.source.source_version, 'evidence_hashes': []})
        for start in range(0, 105, 3):
            ChatMessage.objects.create(conversation_id=self.conversation, role='assistant', status='completed',
                                       request_id=uuid4(), content='Histórico', sources=stored[start:start+3])
        messages = list(ChatMessage.objects.all())
        with CaptureQueriesContext(connection) as queries:
            result = present_sources(messages)
        self.assertEqual(len(queries), 1)
        self.assertEqual(sum(s['available'] for values in result.values() for s in values), 100)

    def test_malformed_stored_reference_does_not_expose_metadata_or_break_history(self):
        self.send()
        message = ChatMessage.objects.get(role='assistant')
        message.sources = [{'source_id': [], 'title': 'MALFORMED PRIVATE TITLE'}]
        message.save(update_fields=['sources'])
        sources = self.assistant(self.client.get('/chatbot/api/session/'))['sources']
        self.assertEqual(sources, [{'available': False}])

    def test_structured_rule_has_public_title_but_no_invented_url(self):
        result = self.client.post('/chatbot/api/messages/', {'conversation_id': self.conversation,
            'request_id': str(uuid4()), 'content': 'Me dan puntos por registrame?'}, content_type='application/json')
        source = self.assistant(result)['sources'][0]
        self.assertEqual(source['type'], 'business_rule')
        self.assertIsNone(source['url'])
        self.assertTrue(source['available'])
        self.assertNotIn('.py', source['title'])
        self.provider.respond.assert_not_called()

    def test_category_leaving_public_taxonomy_hides_historical_reference(self):
        category = Category.objects.create(name='Deporte', slug='deporte')
        self.provider.respond.side_effect = lambda history, *, language, knowledge: GeneratedAnswer((
            EvidenceSelection(knowledge.fragments[0].source.source_id, knowledge.fragments[0].content),))
        result = self.client.post('/chatbot/api/messages/', {'conversation_id': self.conversation,
            'request_id': str(uuid4()), 'content': '¿Qué categorías hay en el catálogo?'}, content_type='application/json')
        self.assertTrue(self.assistant(result)['sources'][0]['available'])
        category.is_active = False
        category.save()
        self.assertEqual(self.assistant(self.client.get('/chatbot/api/session/'))['sources'], [{'available': False}])

    def test_center_hidden_after_local_answer_hides_reference(self):
        center = RecyclingCenter.objects.create(name='Punto Limpio de Adeje', address='Dirección pública',
            latitude=28, longitude=-16, schedule='Lunes de 09:00 a 14:00', is_active=True, show_on_public_map=True)
        result = self.client.post('/chatbot/api/messages/', {'conversation_id': self.conversation,
            'request_id': str(uuid4()), 'content': '¿Cuál es el horario del punto limpio de Adeje?'}, content_type='application/json')
        self.assertTrue(self.assistant(result)['sources'][0]['available'])
        center.refresh_from_db()
        self.assertEqual(self.assistant(result)['sources'][0]['directions_url'], center.directions_url)
        center.show_on_public_map = False
        center.save()
        self.assertEqual(self.assistant(self.client.get('/chatbot/api/session/'))['sources'], [{'available': False}])

    def test_english_reference_labels_are_translated_but_source_title_is_original(self):
        with translation.override('en'):
            page = self.client.get('/')
        self.assertContains(page, 'Reference unavailable')
        self.assertContains(page, 'View official information')
