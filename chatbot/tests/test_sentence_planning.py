"""Offline E34 regressions: actual frozen fixture, both modes, unchanged validators."""
from dataclasses import replace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from faq.models import FAQ
from chatbot.answering import ConversationAnswerer
from chatbot.evaluation.dataset import load_dataset
from chatbot.evaluation.fixtures import install_case
from chatbot.generation import validate_grounded
from chatbot.grounding import EvidenceSelection, GeneratedAnswer, GroundedClaim
from chatbot.models import ChatSessionUsage
from chatbot.response_style import sentence_count, validate_style
from chatbot.safety import ProviderError
from chatbot.sentence_planning import plan_three_sentences
from chatbot.tests.fixtures import knowledge_fixture
from chatbot.tests.test_response_style import OVERVIEW, THREE


QUESTION = 'Explícame cómo funciona TRUEC@ en tres frases'


class SentencePlanningTests(SimpleTestCase):
    def bundle(self, content=OVERVIEW, **kwargs):
        return replace(knowledge_fixture(content), response_sentences=3, **kwargs)

    def test_both_modes_preserve_literal_evidence_and_pass_existing_validators(self):
        for mode in ('extractive', 'grounded'):
            bundle = self.bundle(generation_mode=mode)
            answer = plan_three_sentences(QUESTION, bundle)
            text = answer.text if mode == 'grounded' else answer.selections[0].display_text
            self.assertEqual(text, '\n\n'.join(THREE))
            validate_style(text, language='es', sentences=3)
            if mode == 'grounded':
                self.assertEqual(validate_grounded(answer, bundle), answer)
                self.assertTrue(all(s.content in OVERVIEW for s in answer.selections))
            else:
                self.assertEqual(answer.selections[0].content, OVERVIEW)

    def test_not_a_hardcoded_answer_or_translation(self):
        original = 'Prepara las fotografías y revisa los datos del objeto. Consulta la ayuda de publicación.'
        answer = plan_three_sentences('Resume en tres frases', self.bundle(original))
        self.assertEqual(answer.selections[0].display_text,
                         'Prepara las fotografías.\n\nRevisa los datos del objeto.\n\nConsulta la ayuda de publicación.')
        self.assertIsNone(plan_three_sentences(QUESTION, self.bundle(requested_language='en')))
        english = 'Explore the catalog and submit object proposals for review. Consult the exchange help information.'
        answer = plan_three_sentences('Explain in three sentences', self.bundle(english, requested_language='en', generation_mode='grounded'))
        self.assertEqual(sentence_count(answer.text), 3)
        self.assertIn('Submit object proposals for review.', answer.text)

    def test_declines_incomplete_ambiguous_critical_and_unsafe_evidence(self):
        cases = ('Explora el catálogo. Consulta la ayuda.', OVERVIEW + ' Revisa las publicaciones.',
                 OVERVIEW.replace(' y ', '; '), OVERVIEW.replace('presenta', 'las'),
                 OVERVIEW.replace(' y ', ' o '), OVERVIEW.replace('revisión', 'aprobación'),
                 OVERVIEW.replace(' y ', ' y solo '), OVERVIEW.replace(' y ', ' y si '),
                 OVERVIEW.replace(' y ', ' y no '), OVERVIEW + ' Son 50 puntos.',
                 OVERVIEW + ' https://evil.invalid', OVERVIEW + ' user@example.invalid',
                 OVERVIEW + '<script>alert(1)</script>')
        for content in cases:
            with self.subTest(content=content):
                self.assertIsNone(plan_three_sentences(QUESTION, self.bundle(content)))

    def test_declines_unapproved_rules_multiple_sources_and_unrequested_format(self):
        bundle = self.bundle()
        item = bundle.fragments[0]
        for candidate in (replace(bundle, response_sentences=0), replace(bundle, fragments=(item, item)),
                          replace(bundle, fragments=(replace(item, approval='pending'),)),
                          replace(bundle, fragments=(replace(item, claim_ids=('platform.overview',)),)),
                          replace(bundle, fragments=(replace(item, title='Condiciones y puntos'),))):
            self.assertIsNone(plan_three_sentences(QUESTION, candidate))
        self.assertIsNone(plan_three_sentences('¿Cuesta 50 puntos? En tres frases', bundle))

    def test_existing_style_and_grounding_rejections_remain_strict(self):
        with self.assertRaisesRegex(ProviderError, 'invalid_response_style'):
            validate_style(OVERVIEW, language='es', sentences=3)
        bundle = self.bundle(generation_mode='grounded')
        valid = plan_three_sentences(QUESTION, bundle)
        for citation in (EvidenceSelection('faq:invented', OVERVIEW),
                         EvidenceSelection('faq:1', 'Una cita inventada sobre el catálogo')):
            claims = (GroundedClaim(valid.claims[0].text, (citation,)), *valid.claims[1:])
            with self.assertRaisesRegex(ProviderError, 'invalid_grounding'):
                validate_grounded(GeneratedAnswer((), valid.text, claims), bundle)


@override_settings(CHATBOT_ENABLED=True, CHATBOT_RATE_LIMIT_REQUESTS=1000)
class E34ConversationTests(TestCase):
    def setUp(self):
        cases, _ = load_dataset('pilot-v1')
        self.case = next(c for c in cases if c['id'] == 'E34')
        install_case(self.case)

    def test_actual_e34_in_both_modes_never_constructs_provider(self):
        for mode in ('extractive', 'grounded'):
            with self.subTest(mode=mode), override_settings(CHATBOT_GENERATION_MODE=mode):
                factory = Mock(side_effect=AssertionError('No provider expected'))
                reply = ConversationAnswerer().answer(self.case['question'], language='es', provider_factory=factory)
                self.assertEqual(reply.text, '\n\n'.join(THREE))
                self.assertEqual(reply.route, 'deterministic')
                self.assertEqual(reply.outcome, 'grounded')
                self.assertTrue(reply.references)
                factory.assert_not_called()

    def test_changed_or_withdrawn_source_is_not_exposed(self):
        from chatbot.knowledge import KnowledgeService
        for change in ({'is_published': False}, {'answer': 'Información actualizada.'}):
            install_case(self.case)
            knowledge = KnowledgeService()
            original = knowledge.retrieve
            calls = []
            def retrieve(*args, **kwargs):
                if calls:
                    FAQ.objects.filter(answer=OVERVIEW).update(**change)
                calls.append(True)
                return original(*args, **kwargs)
            with patch.object(knowledge, 'retrieve', side_effect=retrieve):
                factory = Mock()
                reply = ConversationAnswerer(knowledge).answer(QUESTION, language='es', provider_factory=factory)
            self.assertEqual(reply.outcome, 'no_evidence')
            self.assertFalse(reply.references)
            self.assertNotIn(THREE[0], reply.text)
            factory.assert_not_called()

    def test_anonymous_api_persistence_quota_reset_and_duplicate_in_both_modes(self):
        for mode in ('extractive', 'grounded'):
            self.client.cookies.clear()
            with self.subTest(mode=mode), override_settings(CHATBOT_GENERATION_MODE=mode), patch('chatbot.services.get_provider') as factory:
                conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']
                payload = {'conversation_id': conversation, 'request_id': str(uuid4()), 'content': self.case['question']}
                for _ in range(2):
                    result = self.client.post('/chatbot/api/messages/', payload, content_type='application/json')
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()['messages'][-1]['content'], '\n\n'.join(THREE))
                session = self.client.get('/chatbot/api/session/').json()
                self.assertEqual(session['messages'][-1]['content'], '\n\n'.join(THREE))
                self.assertEqual(self.client.post('/chatbot/api/reset/', {}, content_type='application/json').status_code, 200)
                factory.assert_not_called()
        self.assertEqual(list(ChatSessionUsage.objects.values_list('questions_consumed', flat=True)), [1, 1])
        self.assertFalse(ChatSessionUsage.objects.exclude(pending_requests=0).exists())

    def test_authenticated_e34_both_modes(self):
        user = get_user_model().objects.create_user(email='e34@example.invalid', username='e34-offline', password='test-only')
        self.client.force_login(user)
        for mode in ('extractive', 'grounded'):
            with override_settings(CHATBOT_GENERATION_MODE=mode), patch('chatbot.services.get_provider') as factory:
                conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']
                result = self.client.post('/chatbot/api/messages/', {'conversation_id': conversation,
                    'request_id': str(uuid4()), 'content': QUESTION}, content_type='application/json')
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['messages'][-1]['content'], '\n\n'.join(THREE))
                factory.assert_not_called()
        self.assertFalse(ChatSessionUsage.objects.exists())
