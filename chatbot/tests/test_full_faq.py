import json
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from django.test import TestCase, override_settings

from chatbot.answering import ConversationAnswerer
from chatbot.evaluation.dataset import DATA, FULL_FAQ_DATASET, load_dataset
from chatbot.evaluation.runner import answer_case
from chatbot.full_faq_protocol import FullIntent, FullOutput, parse_full_output
from chatbot.knowledge.full_faq import _serialize, load_snapshot
from chatbot.providers import ChatTurn, OpenAIProvider, request_arguments
from chatbot.safety import ProviderError
from chatbot.services import Owner, get_session, send_message
from faq.models import FAQ
from locations.models import RecyclingCenter


@override_settings(CHATBOT_GENERATION_MODE='full_faq')
class FullFAQTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        corpus = json.loads((DATA / 'full-faq-corpus-v1.json').read_text())
        cls.faqs = {key: FAQ.objects.create(**row) for key, row in corpus['faqs'].items()}
        cls.center = RecyclingCenter.objects.create(**corpus['test_center'])

    def reviewed(self, key, source=None):
        return {'kind': 'reviewed', 'source_id': f'faq:{self.faqs[source or key].pk}', 'key': key}

    def answer(self, question, *parts, language='es'):
        return FullOutput(language, (FullIntent('answer', question, parts),))

    def ask(self, question, result, history=(), *, provider=None):
        provider = provider or Mock(spec=['respond'])
        provider.respond.return_value = result
        reply = ConversationAnswerer().answer_message(
            question, [], interface_language='es', provider_factory=lambda: provider, history=history,
        )
        return reply, provider

    def test_complete_public_snapshot_cache_and_bulk_invalidation(self):
        FAQ.objects.create(question='Administración', answer='Borrador privado', is_published=False)
        with self.assertNumQueries(1):
            initial = load_snapshot('es')
        self.assertEqual(len(initial.fragments), 17)
        self.assertTrue(all('reviewed' in faq for faq in json.loads(initial.context)))
        hits = _serialize.cache_info().hits
        self.assertEqual(load_snapshot('en').context_version, initial.context_version)
        self.assertGreater(_serialize.cache_info().hits, hits)
        row = FAQ.objects.create(question='Pregunta nueva', answer='Contenido público completo')
        self.assertIn(row.answer, load_snapshot('es').context)
        FAQ.objects.filter(pk=row.pk).update(answer='Edición masiva')
        self.assertNotIn(row.answer, load_snapshot('es').context)
        FAQ.objects.filter(pk=row.pk).update(is_published=False)
        self.assertEqual(load_snapshot('es').context_version, initial.context_version)
        FAQ.objects.filter(pk=row.pk).update(is_published=True)
        self.assertIn('Edición masiva', load_snapshot('es').context)
        row.delete()
        self.assertEqual(load_snapshot('es').context_version, initial.context_version)

    def test_stable_prefix_and_history_privacy(self):
        knowledge = load_snapshot('es')
        prior = [ChatTurn('user', 'Mi dirección es Calle Privada'), ChatTurn('assistant', 'Calle Privada'),
                 ChatTurn('user', '¿Cómo publico?'), ChatTurn('assistant', 'Añade una foto.')]
        args = request_arguments([*prior, ChatTurn('user', 'And the photos? test@example.test')],
                                 language='en', knowledge=knowledge)
        changed = request_arguments([ChatTurn('user', '¿Cuántos puntos?')], language='es', knowledge=knowledge)
        self.assertEqual(args['input'][0], changed['input'][0])
        self.assertEqual(args['prompt_cache_options'], {'mode': 'explicit', 'ttl': '30m'})
        self.assertEqual(args['input'][0]['content'][0]['prompt_cache_breakpoint'], {'mode': 'explicit'})
        self.assertEqual(args['instructions'], changed['instructions'])
        self.assertEqual(args['text'], changed['text'])
        self.assertFalse(args['store'])
        self.assertTrue(args['text']['format']['strict'])
        dynamic = json.loads(args['input'][1]['content'])
        self.assertEqual([turn['role'] for turn in dynamic['historial']], ['user', 'assistant'])
        self.assertNotIn('test@example.test', dynamic['turno_actual'])
        self.assertNotIn('Calle Privada', str(dynamic))
        with override_settings(CHATBOT_FULL_FAQ_HISTORY_CHARS=2):
            small = request_arguments([*prior, ChatTurn('user', '¿Y la foto?')], language='es', knowledge=knowledge)
        self.assertEqual(json.loads(small['input'][1]['content'])['historial'], [])

    def test_full_budget_fails_before_factory_and_request_budget_before_sdk(self):
        factory = Mock()
        with override_settings(CHATBOT_FULL_FAQ_MAX_CHARS=300):
            reply = ConversationAnswerer().answer_message('¿Cómo publico?', [], interface_language='es',
                                                          provider_factory=factory)
        self.assertEqual(reply.outcome, 'no_evidence')
        factory.assert_not_called()
        with override_settings(CHATBOT_FULL_FAQ_REQUEST_BYTES=300):
            with self.assertRaisesMessage(ProviderError, 'full_faq_budget'):
                request_arguments([ChatTurn('user', '¿Cómo publico?')], language='es', knowledge=load_snapshot('es'))

    def test_reviewed_rules_and_multiple_intents_use_one_call(self):
        result = FullOutput('en', (
            FullIntent('answer', 'How do I earn points', (
                self.reviewed('registration_points', 'points'), self.reviewed('delivery_points', 'points'))),
            FullIntent('answer', 'when do I spend them?', (self.reviewed('reservation_points', 'points'),)),
        ))
        reply, provider = self.ask('How do I earn points and when do I spend them?', result)
        self.assertEqual(reply.text.count('100 points'), 2)
        self.assertIn('reservation does not deduct points', reply.text)
        self.assertIn('50 points are deducted when you collect', reply.text)
        self.assertTrue(all(ref.public_url.endswith(f'#faq-question-{self.faqs["points"].pk}') for ref in reply.references))
        provider.respond.assert_called_once()

    def test_invalid_citations_and_critical_free_text_are_rejected(self):
        points = self.faqs['points']
        for part in (
            {'kind': 'reviewed', 'source_id': 'faq:999999', 'key': 'points'},
            self.reviewed('charge', 'registration'),
            {'kind': 'grounded', 'source_id': f'faq:{points.pk}', 'text': 'La reserva cuesta 50 puntos.',
             'quote': points.answer},
            {'kind': 'grounded', 'source_id': f'faq:{self.faqs["overview"].pk}', 'text': 'Puedes consultar el catálogo.',
             'quote': 'Cita inventada para obtener acceso al catálogo.'},
        ):
            with self.subTest(kind=part['kind'], source=part['source_id']):
                question = '¿Cómo funciona TRUEC@?'
                reply, _ = self.ask(question, self.answer(question, part))
                self.assertEqual(reply.outcome, 'no_evidence')
                self.assertFalse(reply.references)

    def test_withdrawal_or_revision_during_provider_call(self):
        faq = self.faqs['publication']
        for changes in ({'is_published': False}, {'answer': 'Otra revisión publicada'}):
            FAQ.objects.filter(pk=faq.pk).update(is_published=True, answer=faq.answer)
            provider = Mock()
            def change(*args, **kwargs):
                FAQ.objects.filter(pk=faq.pk).update(**changes)
                return self.answer('¿Cómo publico un artículo?', self.reviewed('publication'))
            provider.respond.side_effect = change
            reply, _ = self.ask('¿Cómo publico un artículo?', None, provider=provider)
            self.assertEqual(reply.outcome, 'no_evidence')
            self.assertFalse(reply.references)

    def test_clarification_and_operational_public_data(self):
        clarification, _ = self.ask('¿A qué hora abren?', FullOutput('es', (
            FullIntent('clarification', '¿A qué hora abren?', question='¿De qué centro quieres saber el horario?'),
        )))
        self.assertEqual(clarification.text, '¿De qué centro quieres saber el horario?')
        part = {'kind': 'center', 'name': 'Punto Limpio de Adeje', 'fields': ['address']}
        question = '¿Cuál es la dirección del Punto Limpio de Adeje?'
        reply, _ = self.ask(question, self.answer(question, part))
        self.assertIn(self.center.address, reply.text)
        self.assertEqual(reply.references[0].source_type, 'center')
        live = '¿Está abierto ahora el Punto Limpio de Adeje?'
        reply, _ = self.ask(live, self.answer(live, part))
        self.assertFalse(reply.references)
        self.center.show_on_public_map = False
        self.center.save()
        reply, _ = self.ask(question, self.answer(question, part))
        self.assertFalse(reply.references)

    def test_mixed_intents_keep_valid_answers_and_reject_invalid_sources(self):
        question = 'How do I register, where can I collect it, and can I transfer my points?'
        output = FullOutput('en', (
            FullIntent('answer', 'How do I register', (self.reviewed('registration'),)),
            FullIntent('clarification', 'where can I collect it', question='Which item would you like to collect?'),
            FullIntent('answer', 'can I transfer my points', (
                {'kind': 'reviewed', 'source_id': 'faq:999999', 'key': 'points'},
            )),
        ))
        reply, provider = self.ask(question, output)
        self.assertEqual(reply.outcome, 'partial')
        self.assertIn('registration page', reply.text)
        self.assertIn('Which item would you like to collect?', reply.text)
        self.assertIn('could not verify a response', reply.text)
        self.assertEqual([ref.source_id for ref in reply.references], [f'faq:{self.faqs["registration"].pk}'])
        provider.respond.assert_called_once()

    def test_verified_parts_survive_an_invalid_citation_in_the_same_request(self):
        question = 'How do I publish an item?'
        invalid = {'kind': 'reviewed', 'source_id': 'faq:999999', 'key': 'points'}
        reply, _ = self.ask(question, self.answer(question, self.reviewed('publication'), invalid, language='en'))
        self.assertEqual(reply.outcome, 'partial')
        self.assertIn('Add products', reply.text)
        self.assertIn('could not verify a response', reply.text)
        self.assertEqual(len(reply.references), 1)

    def test_a_curated_clause_renders_only_the_approved_wording(self):
        from chatbot.knowledge.reviewed_faq import UNIT_CLAUSE

        source = self.faqs['delivery_limit']
        question = '¿Cuenta una vajilla como una unidad?'
        part = {'kind': 'grounded', 'source_id': f'faq:{source.pk}', 'quote': UNIT_CLAUSE,
                'text': 'Cada pieza cuenta como un producto y se descuentan 100 puntos.'}
        reply, _ = self.ask(question, self.answer(question, part))
        self.assertEqual(reply.outcome, 'grounded')
        self.assertIn('cuenta como un producto', reply.text)
        self.assertNotIn('100 puntos', reply.text)

    def test_duplicate_abstention_requires_an_identical_resolved_scope(self):
        question = 'How do I register, and do you ship internationally?'
        output = FullOutput('en', (
            FullIntent('answer', 'How do I register', (self.reviewed('registration'),)),
            FullIntent('insufficient_information', 'How do I register'),
            FullIntent('insufficient_information', 'do you ship internationally'),
        ))
        reply, _ = self.ask(question, output)
        self.assertEqual(reply.outcome, 'partial')
        self.assertIn('registration page', reply.text)
        output = FullOutput('en', output.intents[:2])
        reply, _ = self.ask(question, output)
        self.assertEqual(reply.outcome, 'grounded')
        self.assertNotIn('not have enough public information', reply.text)

    def test_reviewed_publication_step_survives_a_grounded_selection_for_the_same_faq(self):
        question = 'Does adding a product require a description, and who checks it at the centre?'
        source = self.faqs['publication']
        evidence = 'Identifícate con tu DNI/NIE al gestor del punto limpio que validará el objeto'
        result = FullOutput('en', (
            FullIntent('answer', 'Does adding a product require a description',
                       (self.reviewed('publication_photos', 'publication'),)),
            FullIntent('answer', 'who checks it at the centre',
                       ({'kind': 'grounded', 'source_id': f'faq:{source.pk}', 'quote': evidence,
                         'text': 'The recycling-point staff member checks the item.'},)),
        ))
        reply, _ = self.ask(question, result)
        self.assertEqual(reply.outcome, 'grounded')
        self.assertIn('photo and description', reply.text)
        self.assertIn('Staff at the recycling point check and validate', reply.text)

    def test_published_hours_are_distinct_from_live_status_in_the_same_turn(self):
        question = 'What are the published hours for Adeje, and is it open right now?'
        part = {'kind': 'center', 'name': self.center.name, 'fields': ['schedule']}
        output = FullOutput('en', (
            FullIntent('answer', 'What are the published hours for Adeje', (part,)),
            FullIntent('answer', 'is it open right now', (part,)),
        ))
        reply, _ = self.ask(question, output)
        self.assertEqual(reply.outcome, 'partial')
        self.assertIn('Monday to Friday, from 08:00 to 15:00', reply.text)
        self.assertIn('not have enough public information', reply.text)
        self.assertEqual(len(reply.references), 1)

    def test_published_timetable_is_independent_of_an_item_available_today(self):
        fixtures = json.loads((DATA / 'full-faq-v4.json').read_text())
        arona = RecyclingCenter.objects.create(**fixtures['test_centers']['arona'])
        timetable = 'the published timetable at Arona'
        availability = 'do they have a spare bike on site today?'
        question = f'What is {timetable}, and {availability}'
        part = {'kind': 'center', 'name': arona.name, 'fields': ['schedule']}
        output = FullOutput('en', (
            FullIntent('answer', timetable, (part,)),
            FullIntent('insufficient_information', availability),
        ))

        reply, _ = self.ask(question, output)

        self.assertEqual(reply.outcome, 'partial')
        self.assertIn('Published hours: Tuesday to Friday: 09:00–17:00', reply.text)
        self.assertIn('not have enough public information', reply.text)
        self.assertEqual([ref.source_id for ref in reply.references], [f'center:{arona.pk}'])

        today_question = 'What is the timetable at Arona today?'
        reply, _ = self.ask(today_question, self.answer(today_question, part, language='en'))
        self.assertEqual(reply.outcome, 'no_evidence')
        self.assertFalse(reply.references)

    def test_english_clarification_preserves_spanish_public_center_names(self):
        other = RecyclingCenter.objects.create(
            name='Punto Limpio de La Guancha', slug='guancha-pruebas', address='Calle de Pruebas 23',
            latitude=self.center.latitude, longitude=self.center.longitude, show_on_public_map=True,
        )
        question = 'How do I register and what is the centre address?'
        clarification = f'Which centre: {self.center.name} or {other.name}?'
        reply, _ = self.ask(question, FullOutput('en', (
            FullIntent('answer', 'How do I register', (self.reviewed('registration'),)),
            FullIntent('clarification', 'what is the centre address?', question=clarification),
        )))
        self.assertEqual(reply.outcome, 'partial')
        self.assertIn('registration page', reply.text)
        self.assertIn(clarification, reply.text)

    def test_center_phone_and_mismatched_historical_center(self):
        self.center.phone = '922 000 001'
        self.center.save(update_fields=['phone'])
        part = {'kind': 'center', 'name': self.center.name, 'fields': ['phone']}
        question = 'What is the phone number for Adeje?'
        reply, _ = self.ask(question, self.answer(question, part, language='en'))
        self.assertIn('Phone: 922 000 001', reply.text)
        unrelated = 'What is the phone number for La Laguna?'
        history = (ChatTurn('user', 'Where is Adeje?'),)
        reply, _ = self.ask(unrelated, self.answer(unrelated, part, language='en'), history)
        self.assertEqual(reply.outcome, 'no_evidence')

    @override_settings(CHATBOT_ENABLED=True, CHATBOT_ANONYMOUS_LIMIT=10)
    def test_anonymous_service_sends_assistant_history_and_keeps_existing_quota(self):
        owner = Owner(session_key='test-session-full-faq')
        session = get_session(owner)
        conversation = session['conversation']['id']
        provider = Mock(spec=['respond'])
        provider.respond.side_effect = [
            self.answer('¿Cómo puedo publicar un artículo?', self.reviewed('publication')),
            self.answer('And the photos?', self.reviewed('publication_photos', 'publication'), language='en'),
        ]
        send_message(owner, conversation, uuid4(), '¿Cómo puedo publicar un artículo?', provider=provider)
        response, status = send_message(owner, conversation, uuid4(), 'And the photos?', provider=provider)
        history = provider.respond.call_args.args[0]
        self.assertEqual([turn.role for turn in history], ['user', 'assistant', 'user'])
        self.assertIn('Añadir productos', history[1].content)
        self.assertEqual(status, 200)
        self.assertEqual(response['usage']['remaining'], 8)
        self.assertIn('photo and description', response['messages'][-1]['content'])
        self.assertEqual(provider.respond.call_count, 2)

    def test_structured_output_and_sdk_telemetry(self):
        raw = json.dumps({'language': 'es', 'intents': [
            {'status': 'answer', 'request': '¿Cómo puedo publicar un artículo?', 'parts': [self.reviewed('publication')]},
        ]})
        response = Mock(status='completed', error=None, output_text=raw, model='gpt-6-luna')
        response.usage.input_tokens = 3000
        response.usage.output_tokens = 45
        response.usage.input_tokens_details.cached_tokens = 2000
        response.usage.input_tokens_details.cache_write_tokens = 0
        sdk = AsyncMock(return_value=response)
        with patch('chatbot.providers.require_openai_access'), patch.object(OpenAIProvider, '_request', sdk):
            provider = OpenAIProvider()
            reply, _ = self.ask_sdk(provider)
        self.assertEqual(reply.outcome, 'grounded')
        sdk.assert_awaited_once()
        self.assertEqual(provider.last_usage['cached_tokens'], 2000)
        self.assertEqual(provider.last_usage['estimated_usd'], 0.0001425)
        with self.assertRaises(ProviderError):
            parse_full_output('{"language":"es","intents":[{"status":"insufficient_information","request":"horario","parts":[]}]}')

    def ask_sdk(self, provider):
        question = '¿Cómo puedo publicar un artículo?'
        return ConversationAnswerer().answer_message(question, [], interface_language='es',
                                                     provider_factory=lambda: provider), provider

    def test_versioned_cases_replay_expected_contract_without_a_model(self):
        cases, _ = load_dataset(FULL_FAQ_DATASET)
        self.assertGreaterEqual(len(cases), 80)
        self.assertEqual({case['language'] for case in cases}, {'es', 'en'})
        for case in cases:
            with self.subTest(case=case['id']):
                parts = tuple(
                    {'kind': 'center', 'name': self.center.name, 'fields': [fact]}
                    if source == 'center' else self.reviewed(fact, source)
                    for source, fact in zip(case['expected_sources'], case['expected_facts'], strict=True)
                )
                question = ('¿Puedes indicar el centro o el artículo?' if case['language'] == 'es'
                            else 'Which centre or item do you mean?') if case['expected_status'] == 'clarification' else ''
                provider = Mock(spec=['respond'])
                provider.respond.return_value = FullOutput(case['language'], (
                    FullIntent(case['expected_status'], case['question'], parts, question),
                ))
                reply = answer_case(case, provider)
                wanted = {'answer': 'grounded', 'clarification': 'clarification',
                          'insufficient_information': 'no_evidence'}[case['expected_status']]
                self.assertEqual(reply.outcome, wanted)
                self.assertEqual({ref.source_id for ref in reply.references},
                                 {f'center:{self.center.pk}' if key == 'center' else f'faq:{self.faqs[key].pk}'
                                  for key in case['expected_sources']})
                provider.respond.assert_called_once()
