"""Pilot regressions with one mocked Responses call and no network access."""
import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import translation

from faq.models import FAQ
from chatbot.answering import ConversationAnswerer
from chatbot.generation import parse_grounded
from chatbot.grounding import EvidenceSelection, GeneratedAnswer, GroundedClaim, parse_answer, response_format
from chatbot.models import ChatMessage, ChatSessionUsage
from chatbot.providers import OpenAIProvider, ChatTurn, request_arguments
from chatbot.response_style import (requested_sentences, sentence_count, validate_display_text,
                                    validate_style, with_response_style)
from chatbot.safety import ProviderError
from chatbot.tests.fixtures import knowledge_fixture
from chatbot.tests.test_openai_provider import response


SOURCE = ('Inicia sesión y abre la opción de publicación. Completa los datos del objeto y añade fotografías. '
          'Envía la propuesta al equipo para su revisión.')
ENGLISH = ('Sign in and open the publication option. Complete the object details and add photographs. '
           'Submit the proposal to the team for review.')
OVERVIEW = ('Explora el catálogo y presenta propuestas de objetos para revisión. '
            'Consulta la ayuda para conocer el proceso de intercambio.')
THREE = ('Explora el catálogo.', 'Presenta propuestas de objetos para revisión.',
         'Consulta la ayuda para conocer el proceso de intercambio.')
THREE_EN = ('Explore the catalog.', 'Submit object proposals for review.',
            'Consult the help information about the exchange process.')


def selected(knowledge, text):
    f = knowledge.fragments[0]
    return GeneratedAnswer((EvidenceSelection(f.source.source_id, f.content, text),))


def grounded(knowledge, texts):
    evidence = (EvidenceSelection(knowledge.fragments[0].source.source_id, knowledge.fragments[0].content),)
    return GeneratedAnswer(evidence, '\n\n'.join(texts), tuple(GroundedClaim(t, evidence) for t in texts))


class ResponseStyleUnitTests(SimpleTestCase):
    def test_supported_style_requests_and_plain_business_questions(self):
        for question in ('Explica en tres frases', 'Explica en 3 frases', 'Explain in three sentences', 'Explain in 3 sentences'):
            self.assertEqual(requested_sentences(question), 3)
        for question in ('¿Tengo tres reservas?', '¿Tengo 3 días para recoger?', 'Three objects', 'en tres párrafos'):
            self.assertEqual(requested_sentences(question), 0)

    def test_sentence_count_handles_decimals_abbreviations_initials_and_punctuation(self):
        for text in ('Hola. ¿Cómo estás? Bien.', 'Hello! Welcome? Thanks.',
                     'El Dr. Pérez consulta. El valor es 1.25. Revisa la U.E. ahora.'):
            self.assertEqual(sentence_count(text), 3)
        with self.assertRaisesRegex(ProviderError, 'invalid_response_style'):
            validate_style('Una frase. Otra frase. Fragmento', language='es', sentences=3)

    def test_wrong_language_and_english_preamble_over_spanish_body_are_rejected(self):
        for text in (SOURCE, 'According to the public information. ' + SOURCE):
            with self.assertRaisesRegex(ProviderError, 'invalid_response_style'):
                validate_style(text, language='en-GB')
        with self.assertRaisesRegex(ProviderError, 'invalid_response_style'):
            validate_style(ENGLISH, language='es')
        validate_style(ENGLISH, language='en_US', sentences=3)

    def test_translation_retains_exact_original_selection_and_rejects_missing_display(self):
        bundle = with_response_style(knowledge_fixture(SOURCE), 'Explain how to publish', 'en')
        payload = {'selections': [{'source_id': 'faq:1', 'content': SOURCE, 'display_text': ENGLISH}]}
        parsed = parse_answer(json.dumps(payload), bundle)
        self.assertEqual(parsed.selections[0].content, SOURCE)
        self.assertEqual(parsed.selections[0].display_text, ENGLISH)
        del payload['selections'][0]['display_text']
        with self.assertRaises(ProviderError):
            parse_answer(json.dumps(payload), bundle)

    def test_presentation_cannot_invent_facts_links_personal_data_or_drop_constraints(self):
        cases = ((ENGLISH + ' You receive 999 points.', SOURCE),
                 ('Publishing is approved.', SOURCE), ('The moon has chocolate bicycles.', SOURCE),
                 (ENGLISH + ' https://evil.invalid', SOURCE), (ENGLISH + '<script>x</script>', SOURCE),
                 (ENGLISH + ' alice@example.invalid', SOURCE),
                 ('You can reserve your own object.', 'No puedes reservar un objeto propio.'),
                 ('Submit the proposal for review.', 'Envía la propuesta solo si cumple las condiciones.'),
                 ('You have 9 objects.', 'Tienes 2 objetos.'))
        for text, source in cases:
            with self.subTest(text=text), self.assertRaises(ProviderError):
                validate_display_text(text, source, language='en')

    def test_critical_units_require_literal_text_and_do_not_gain_translation_permissions(self):
        with self.assertRaisesRegex(ProviderError, 'invalid_response_style'):
            validate_display_text('You cannot reserve your own object.', 'No puedes reservar un objeto propio.', language='en')
        text = 'You cannot reserve your own object.'
        validate_display_text(text, text, language='en')

    def test_grounded_three_sentence_schema_keeps_empty_abstention_possible(self):
        bundle = replace(knowledge_fixture(OVERVIEW), generation_mode='grounded', response_sentences=3)
        schema = response_format(bundle)['schema']['properties']['claims']
        self.assertEqual([s['maxItems'] for s in schema['anyOf']], [3, 0])
        self.assertFalse(parse_grounded('{"answer":"","claims":[],"sufficient":false}', bundle).sufficient)


@override_settings(CHATBOT_ENABLED=True, CHATBOT_RATE_LIMIT_REQUESTS=1000)
class PresentationConversationTests(TestCase):
    def setUp(self):
        self.faq = FAQ.objects.create(question='¿Cómo publicar un objeto?', answer=SOURCE)

    def answer(self, question, language, mode, responder, *, expected_calls=1):
        provider = Mock(respond=Mock(side_effect=responder))
        with override_settings(CHATBOT_GENERATION_MODE=mode):
            reply = ConversationAnswerer().answer(question, language=language, provider_factory=lambda: provider)
        self.assertEqual(provider.respond.call_count, expected_calls)
        return reply

    def test_english_extractive_and_grounded_fallback_keep_public_original_citations(self):
        # A critical but unrelated retrieved FAQ forces extractive, as in pilot E36.
        FAQ.objects.create(question='Publicar objetos y condiciones', answer='No publiques documentos personales.')
        for mode in ('extractive', 'grounded'):
            with self.subTest(mode=mode):
                def respond(history, *, language, knowledge):
                    self.assertEqual(knowledge.generation_mode, 'extractive')
                    f = next(f for f in knowledge.fragments if f.content == SOURCE)
                    return GeneratedAnswer((EvidenceSelection(f.source.source_id, SOURCE, ENGLISH),))
                reply = self.answer('Explain how to publish an object', 'en-GB', mode, respond)
                self.assertEqual(reply.text, ENGLISH)
                self.assertNotIn('Inicia sesión', reply.text)
                self.assertEqual(reply.references[0].supported_text, SOURCE)
                self.assertEqual(reply.references[0].title, self.faq.question)

    def test_three_sentences_in_both_modes_and_languages(self):
        self.faq.question = '¿Cómo funciona TRUEC@?'
        self.faq.answer = OVERVIEW
        self.faq.save()
        for mode in ('extractive', 'grounded'):
            for language, texts, question in (
                ('es', THREE, 'Explícame cómo funciona TRUEC@ en tres frases'),
                ('en', THREE_EN, 'Explain how TRUEC@ works in three sentences')):
                with self.subTest(mode=mode, language=language):
                    def respond(history, *, language, knowledge):
                        self.assertEqual(knowledge.response_sentences, 3)
                        return grounded(knowledge, texts) if mode == 'grounded' else selected(knowledge, ' '.join(texts))
                    reply = self.answer(question, language, mode, respond, expected_calls=0 if language == 'es' else 1)
                    self.assertEqual(sentence_count(reply.text), 3)
                    self.assertTrue(reply.references)

    def test_incorrect_two_sentence_response_is_rejected_without_second_call(self):
        # Complex punctuation deliberately exercises the provider path, not the local formatter.
        self.faq.question, self.faq.answer = '¿Cómo funciona TRUEC@?', OVERVIEW.replace(' y ', '; ')
        self.faq.save()
        for mode in ('extractive', 'grounded'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ProviderError, 'invalid_response_style'):
                self.answer('Explícame cómo funciona TRUEC@ en tres frases', 'es', mode,
                            lambda h, *, language, knowledge: grounded(knowledge, (OVERVIEW,)) if mode == 'grounded' else selected(knowledge, OVERVIEW))

    def test_changed_source_still_invalidates_english_presentation(self):
        def change(history, *, language, knowledge):
            self.faq.is_published = False
            self.faq.save()
            return selected(knowledge, ENGLISH)
        reply = self.answer('Explain how to publish an object', 'en', 'extractive', change)
        self.assertFalse(reply.references)
        self.assertEqual(reply.outcome, 'no_evidence')
        self.assertNotIn(ENGLISH, reply.text)

    def test_api_persists_english_history_and_counts_duplicate_once(self):
        with translation.override('en'), patch('chatbot.services.get_provider') as factory:
            factory.return_value.respond.side_effect = lambda h, *, language, knowledge: selected(knowledge, ENGLISH)
            conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']
            payload = {'conversation_id': conversation, 'request_id': str(uuid4()),
                       'content': 'Explain how to publish an object in three sentences'}
            for _ in range(2):
                result = self.client.post('/chatbot/api/messages/', payload, content_type='application/json')
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['messages'][-1]['content'], ENGLISH)
            factory.return_value.respond.assert_called_once()
            self.assertEqual(self.client.get('/chatbot/api/session/').json()['messages'][-1]['content'], ENGLISH)
            self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 1)

    def test_invalid_language_consumes_no_quota_and_no_raw_response_is_persisted(self):
        with translation.override('en'), patch('chatbot.services.get_provider') as factory:
            factory.return_value.respond.side_effect = lambda h, *, language, knowledge: selected(knowledge, SOURCE)
            conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']
            result = self.client.post('/chatbot/api/messages/', {'conversation_id': conversation,
                'request_id': str(uuid4()), 'content': 'Explain how to publish an object'}, content_type='application/json')
        self.assertEqual(result.status_code, 502)
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 0)
        self.assertEqual(ChatSessionUsage.objects.get().pending_requests, 0)
        self.assertFalse(ChatMessage.objects.filter(role='assistant').exists())
        factory.return_value.respond.assert_called_once()

    def test_invalid_three_sentence_response_consumes_no_quota_or_duplicate_call(self):
        with override_settings(CHATBOT_GENERATION_MODE='extractive'), patch('chatbot.services.get_provider') as factory:
            factory.return_value.respond.side_effect = lambda h, *, language, knowledge: selected(
                knowledge, 'Inicia sesión y añade fotografías. Envía la propuesta al equipo para su revisión.')
            conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']
            payload = {'conversation_id': conversation, 'request_id': str(uuid4()),
                       'content': 'Resume cómo publicar un objeto en tres frases'}
            for _ in range(2):
                result = self.client.post('/chatbot/api/messages/', payload, content_type='application/json')
                self.assertEqual(result.status_code, 502)
            factory.return_value.respond.assert_called_once()
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 0)
        self.assertEqual(ChatSessionUsage.objects.get().pending_requests, 0)
        self.assertFalse(ChatMessage.objects.filter(role='assistant').exists())


@override_settings(CHATBOT_ENABLED=True, CHATBOT_PROVIDER='openai', OPENAI_API_KEY='offline-test-key')
class PresentationSDKTests(SimpleTestCase):
    def test_english_schema_and_instructions_use_one_mocked_request(self):
        factory = MagicMock()
        create = AsyncMock(return_value=response(json.dumps({'selections': [
            {'source_id': 'faq:1', 'content': SOURCE, 'display_text': ENGLISH}]})))
        factory.return_value.__aenter__.return_value.responses.create = create
        with patch('chatbot.safety.is_test_process', return_value=False), patch('chatbot.providers.AsyncOpenAI', factory):
            result = OpenAIProvider().respond([ChatTurn('user', 'Explain how to publish')],
                                              language='en-GB', knowledge=knowledge_fixture(SOURCE))
        self.assertEqual(result.selections[0].display_text, ENGLISH)
        self.assertEqual(result.selections[0].content, SOURCE)
        kwargs = create.call_args.kwargs
        self.assertIn('display_text', kwargs['text']['format']['schema']['properties']['selections']['items']['required'])
        self.assertIn('content sigue siendo la evidencia literal', kwargs['instructions'])
        self.assertEqual(factory.call_args.kwargs['max_retries'], 0)
        create.assert_awaited_once()

    def test_sentence_request_enters_backend_instructions_not_only_user_data(self):
        bundle = replace(knowledge_fixture(OVERVIEW), generation_mode='grounded')
        for question in ('Explica en tres frases', 'Explain in 3 sentences'):
            arguments = request_arguments([ChatTurn('user', question)], language='en', knowledge=bundle)
            self.assertIn('exactamente 3 frases', arguments['instructions'])
            self.assertIn('anyOf', arguments['text']['format']['schema']['properties']['claims'])

    def test_three_grounded_sentences_parse_through_the_mocked_sdk_in_both_languages(self):
        for language, texts in (('es', THREE), ('en', THREE_EN)):
            with self.subTest(language=language):
                factory = MagicMock()
                create = AsyncMock(return_value=response(json.dumps({
                    'answer': '\n\n'.join(texts), 'sufficient': True,
                    'claims': [{'text': t, 'evidence': [{'source_id': 'faq:1', 'quote': OVERVIEW}]} for t in texts]})))
                factory.return_value.__aenter__.return_value.responses.create = create
                with patch('chatbot.safety.is_test_process', return_value=False), patch('chatbot.providers.AsyncOpenAI', factory):
                    result = OpenAIProvider().respond([ChatTurn('user', 'Explain in three sentences')],
                        language=language, knowledge=replace(knowledge_fixture(OVERVIEW), generation_mode='grounded'))
                self.assertEqual(result.text, '\n\n'.join(texts))
                self.assertEqual(sentence_count(result.text), 3)
                create.assert_awaited_once()
