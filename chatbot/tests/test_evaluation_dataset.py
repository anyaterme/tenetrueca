"""Independent oracle invariants and bounded measurement functions; not semantic grading."""
from collections import Counter
from django.test import SimpleTestCase, TestCase, override_settings
from chatbot.evaluation.dataset import load_dataset
from chatbot.evaluation.fixtures import install_case
from chatbot.evaluation.offline import distribution, source_metrics
from chatbot.answering import ConversationAnswerer
from chatbot.providers import SimulatedProvider


class DatasetSpecificationTests(SimpleTestCase):
    def test_ninety_versioned_cases_and_ten_question_pilot(self):
        cases,checksum=load_dataset('phase2-v1');pilot,pilot_checksum=load_dataset('pilot-v1')
        self.assertEqual(len(cases),90);self.assertEqual(len(pilot),10)
        self.assertEqual(len({c['id'] for c in cases}),90)
        self.assertEqual(len(checksum),64);self.assertNotEqual(checksum,pilot_checksum)
        self.assertTrue({'es','en'}.issubset({c['language'] for c in cases}))
        self.assertGreaterEqual(len({c['category'] for c in cases}),12)
        required={'id','category','question','language','history','expected_sources','claims',
                  'required_conditions','required_negations','blocked_claims','expected_behavior',
                  'appropriate_mode','openai_needed','criteria'}
        self.assertTrue(all(required.issubset(c) for c in cases))

    def test_approved_oracles_preserve_events_and_block_unapproved_claims(self):
        cases,_=load_dataset('phase2-v1')
        self.assertTrue(any('activ' in group for group in cases[1]['required_conditions']))
        self.assertTrue(any('fisic' in group for group in cases[3]['required_conditions']))
        self.assertIn('points.reservation_charge',cases[4]['blocked_claims'])
        self.assertIn('reservation.pickup_window',cases[5]['blocked_claims'])
        self.assertIn('admission.small_appliance',cases[12]['blocked_claims'])
        self.assertFalse(any(c['claims'] and set(c['claims']) & set(c['blocked_claims']) for c in cases))

    def test_precision_recall_and_percentiles_are_diagnostic_not_truth_scores(self):
        result=source_metrics(['a','b'],['a','c'])
        self.assertEqual(result['precision_at_k'],.5);self.assertEqual(result['recall_at_k'],.5)
        self.assertIsNone(source_metrics([],[])['recall_at_k'])
        values=distribution(list(range(1,21)))
        self.assertEqual(values,{'n':20,'median':10.5,'p95':19,'max':20})


@override_settings(CHATBOT_GENERATION_MODE='grounded')
class ResponseStyleRegressionTests(TestCase):
    def test_numeric_sentence_request_is_not_a_business_limit_filter(self):
        from unittest.mock import Mock
        from chatbot.grounding import EvidenceSelection, GeneratedAnswer, GroundedClaim
        cases,_=load_dataset('phase2-v1');case=next(c for c in cases if 'en 3 frases' in c['question'])
        install_case(case)
        def respond(history, *, language, knowledge):
            f = next(f for f in knowledge.fragments if 'Inicia sesión' in f.content)
            evidence = (EvidenceSelection(f.source.source_id, f.content),)
            texts = ('Inicia sesión y abre la opción de publicación.',
                     'Completa los datos del objeto y añade fotografías.',
                     'Envía la propuesta al equipo para su revisión.')
            if knowledge.generation_mode == 'grounded':
                return GeneratedAnswer(evidence, '\n\n'.join(texts), tuple(GroundedClaim(t, evidence) for t in texts))
            return GeneratedAnswer((EvidenceSelection(f.source.source_id, f.content, ' '.join(texts)),))
        answer=ConversationAnswerer().answer(case['question'],language='es',provider_factory=lambda: Mock(respond=respond))
        self.assertTrue(answer.references)
        self.assertEqual(answer.outcome,'grounded')

    def test_private_delivery_address_is_abstention_without_retrieving_centers(self):
        from unittest.mock import patch
        for question in ('¿Cuál es mi dirección de entrega?', 'What is my delivery address?', 'Consulta mis reservas particulares'):
            with self.subTest(question=question), patch('chatbot.answering.KnowledgeService.retrieve') as retrieve:
                answer=ConversationAnswerer().answer(question,language='es',provider_factory=SimulatedProvider)
                self.assertEqual(answer.outcome,'no_evidence')
                self.assertIn('privados',answer.text)
                retrieve.assert_not_called()

    def test_publication_english_equivalent_recovers_public_procedure(self):
        from unittest.mock import Mock
        from chatbot.grounding import EvidenceSelection, GeneratedAnswer, GroundedClaim
        cases,_=load_dataset('phase2-v1');case=next(c for c in cases if c['id']=='E37')
        install_case(case)
        def respond(history, *, language, knowledge):
            f=next(f for f in knowledge.fragments if 'Inicia sesión' in f.content)
            text='Sign in and open the publication option. Complete the object details and add photographs. Submit the proposal to the team for review.'
            if knowledge.generation_mode == 'grounded':
                evidence=(EvidenceSelection(f.source.source_id,f.content),)
                return GeneratedAnswer(evidence,text,(GroundedClaim(text,evidence),))
            return GeneratedAnswer((EvidenceSelection(f.source.source_id,f.content,text),))
        answer=ConversationAnswerer().answer(case['question'],language='en',provider_factory=lambda: Mock(respond=respond))
        self.assertTrue(answer.references)
        self.assertIn('Sign in', answer.text)
        self.assertNotIn('Inicia sesión', answer.text)

class EvaluationEnvironmentGuardsTests(SimpleTestCase):
    def test_activation_environment_authorization_and_startup_guards_cannot_be_bypassed(self):
        from unittest.mock import patch
        from uuid import uuid4
        from django.test import override_settings
        from chatbot.evaluation.access import require_evaluation_access
        key=uuid4()
        baseline=dict(CHATBOT_ENABLED=True,CHATBOT_PROVIDER='openai',OPENAI_API_KEY='offline-only',
            CHATBOT_EVAL_ALLOW_LIVE=True,CHATBOT_EVAL_ENVIRONMENT='staging',
            CHATBOT_EVAL_PROJECT_REF='proj_synthetic',CHATBOT_EVAL_AUTHORIZATION_ID=str(key))
        variations=[{'CHATBOT_EVAL_ALLOW_LIVE':False},{'CHATBOT_EVAL_ENVIRONMENT':'production'},
                    {'CHATBOT_EVAL_PROJECT_REF':''},{'CHATBOT_EVAL_AUTHORIZATION_ID':str(uuid4())},
                    {'CHATBOT_ENABLED':False},{'CHATBOT_PROVIDER':'mock'},{'OPENAI_API_KEY':''}]
        for variation in variations:
            with self.subTest(variation=variation),override_settings(**{**baseline,**variation}), \
                 patch('chatbot.safety.is_test_process',return_value=False),self.assertRaises(RuntimeError):
                require_evaluation_access(key)
        with override_settings(**baseline),patch('chatbot.safety.is_test_process',return_value=False), \
             patch('sys.argv',['manage.py','migrate']),self.assertRaises(RuntimeError):
            require_evaluation_access(key)

@override_settings(CHATBOT_ENABLED=True)
class ApprovedPointPolicySecurityTests(TestCase):
    def test_public_faq_cannot_override_approved_awards_or_drop_activation_condition(self):
        from faq.models import FAQ
        from unittest.mock import Mock
        for text in ('Registrarse proporciona 999 puntos.', 'Registrarse proporciona 100 puntos.',
                     'Publicar un objeto genera 100 puntos.', 'Publishing an object gives 100 points.',
                     'Publicar un objeto genera puntos.'):
            FAQ.objects.all().delete()
            FAQ.objects.create(question='¿Cómo funcionan los puntos?',answer=text)
            for mode in ('extractive','grounded'):
                with self.subTest(text=text,mode=mode),override_settings(CHATBOT_GENERATION_MODE=mode):
                    factory=Mock()
                    answer=ConversationAnswerer().answer('¿Cómo funcionan los puntos?',language='es',provider_factory=factory)
                    self.assertEqual(answer.outcome,'no_evidence')
                    self.assertNotIn(text,answer.text)
                    factory.assert_not_called()

    def test_exact_approved_faq_unit_can_still_be_retrieved(self):
        from faq.models import FAQ
        from chatbot.knowledge import KnowledgeService
        text='Recibes 100 puntos cuando completas el registro público y activas tu cuenta.'
        faq=FAQ.objects.create(question='Puntos por registro',answer=text)
        bundle=KnowledgeService().retrieve('registro puntos')
        self.assertIn(f'faq:{faq.pk}',[f.source.source_id for f in bundle.fragments])
