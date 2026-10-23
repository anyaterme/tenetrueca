import json
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

from assistant.fixtures import simulated_response
from assistant.models import EvaluationAttempt
from assistant.providers import MockProvider, OpenAIProvider, ProviderResponse
from assistant.rules import PHOTO_PREFILL_INSTRUCTIONS, PHOTO_PREFILL_VERSION, SYSTEM_INSTRUCTIONS
from assistant.services import current_evaluation, process_evaluation
from assistant.tests import test_photo_first as photo_flow
from audit.models import AuditEvent
from publications.models import Publication


@override_settings(OPENAI_EVALUATION_BACKEND='mock', OPENAI_MODEL='fixture-model')
class PublicDescriptionTests(TestCase):
    setUp = photo_flow.PhotoFirstTests.setUp
    provider = photo_flow.PhotoFirstTests.provider
    start = photo_flow.PhotoFirstTests.start
    form_data = photo_flow.PhotoFirstTests.form_data
    confirm = photo_flow.PhotoFirstTests.confirm

    def test_public_text_and_additional_information_stay_in_their_own_fields(self):
        scenarios = [
            ('Jarrón azul de cuerpo redondeado y boca estrecha.',
             'No se puede establecer el estado interno del objeto.', 'Comprueba su estado real.'),
            ('Dos recipientes azules de distinto tamaño y borde redondeado.',
             'No se puede confirmar si ambos forman un conjunto.', 'Indica qué piezas entregas.'),
            ('Mesa blanca con cuatro patas y una marca visible en el tablero.',
             'Las fotografías no permiten comprobar su estabilidad.', 'Comprueba su estabilidad.'),
            ('Recipiente transparente sin grietas visibles.',
             'No se puede confirmar su material.', 'Confirma el material si lo conoces.'),
            (None, 'No hay información visual suficiente para describir el objeto.',
             'Añade una fotografía nítida del objeto completo.'),
        ]
        for description, uncertainty, recommendation in scenarios:
            with self.subTest(description=description):
                provider = self.provider(description=description)
                payload = provider.evaluate.return_value.payload
                payload['unverifiable'] = [uncertainty]
                payload['recommendations'] = [recommendation]
                p, url = self.start(provider=provider)
                response = self.client.get(url)
                form = response.context['form']
                self.assertEqual(form['description'].value(), description or '')
                self.assertNotIn(uncertainty, str(form['description']))
                self.assertNotIn(recommendation, str(form['description']))
                self.assertContains(response, 'Aspectos pendientes de verificar')
                self.assertContains(response, uncertainty)
                self.assertContains(response, 'Recomendaciones del análisis')
                self.assertContains(response, recommendation)
                self.assertEqual(p.working_data['description'], description or '')
                if description is None:
                    invalid = self.confirm(p, url)
                    self.assertIn('description', invalid.context['form'].errors)
                provider.evaluate.assert_called_once()
        self.assertFalse(Publication.objects.exists())
        self.assertFalse(EvaluationAttempt.objects.filter(is_simulated=False).exists())

    def test_default_mock_puts_completion_advice_outside_the_public_description(self):
        p, url = self.start(provider=MockProvider())
        page = self.client.get(url)
        description = page.context['form']['description'].value()
        self.assertEqual(description, 'Jarrón azul de cuerpo redondeado y boca estrecha.')
        self.assertNotIn('completa', description.lower())
        self.assertContains(page, 'Simulación de desarrollo')
        self.assertIn('Simulación: completa el estado y los detalles reales del objeto.',
                      p.evaluations.get().result['recommendations'])

    @override_settings(OPENAI_API_KEY='fake-key')
    def test_mocked_sdk_receives_public_description_guidance_and_nullable_strict_schema(self):
        p, _ = self.start()
        evaluation = p.evaluations.get()
        fake_response = Mock(
            status='completed', output_text=json.dumps(self.provider().evaluate.return_value.payload),
            id='resp_public_description_fixture', usage=Mock(input_tokens=100, output_tokens=200),
            output=[], incomplete_details=None,
        )

        def api_fixture(**kwargs):
            self.assertEqual(kwargs['instructions'], SYSTEM_INSTRUCTIONS + PHOTO_PREFILL_INSTRUCTIONS)
            self.assertIn('texto que otros usuarios leerán', kwargs['instructions'])
            schema = kwargs['text']['format']['schema']
            prefill = schema['$defs']['PhotoPrefill']
            self.assertFalse(prefill['additionalProperties'])
            self.assertEqual(set(prefill['required']), {'title', 'description', 'category_id'})
            description = prefill['properties']['description']
            self.assertIn('listo para publicar', description['description'])
            self.assertIn('unverifiable', description['description'])
            self.assertIn({'type': 'null'}, description['anyOf'])
            text = next(branch for branch in description['anyOf'] if branch['type'] == 'string')
            self.assertEqual((text['minLength'], text['maxLength']), (1, 4000))
            self.assertFalse(kwargs['store'])
            return fake_response

        with patch('assistant.providers.require_live_access'), patch('openai.OpenAI') as factory:
            client = factory.return_value.__enter__.return_value
            client.responses.create.side_effect = api_fixture
            response = OpenAIProvider().evaluate(evaluation, list(p.photos.all()))
            client.responses.create.assert_called_once()
        self.assertEqual(response.input_tokens, 100)
        self.assertFalse(EvaluationAttempt.objects.filter(is_simulated=False).exists())

    def test_new_version_requires_explicit_analysis_and_preserves_history_and_manual_text(self):
        with patch('assistant.preparations.PHOTO_PREFILL_VERSION', 'photos-first-1'):
            p, url = self.start(provider=self.provider(
                description='Texto anterior. No se puede establecer el estado.'))
        old = p.evaluations.get()
        old_result = old.result.copy()
        with patch('assistant.services.get_provider') as api:
            page = self.client.get(url)
        api.assert_not_called()
        self.assertEqual(page.context['form']['description'].value(), old_result['prefill']['description'])
        provider = self.provider(description='Nueva propuesta publicable.')
        manual = 'Descripción escrita por la persona: no se puede confirmar si van juntos.'
        with patch('assistant.services.get_provider', return_value=provider):
            self.client.post(url, self.form_data(p, action='analyze_photos', description=manual))
        p.refresh_from_db()
        old.refresh_from_db()
        provider.evaluate.assert_called_once()
        self.assertEqual(p.working_data['description'], manual)
        self.assertEqual(old.result, old_result)
        self.assertEqual(old.snapshot['prefill_version'], 'photos-first-1')
        self.assertEqual(p.evaluations.first().snapshot['prefill_version'], PHOTO_PREFILL_VERSION)
        self.assertEqual(p.evaluations.count(), 2)

    def test_operator_review_and_audit_use_the_final_public_description(self):
        provider = self.provider(description='Dos recipientes azules de distinto tamaño.')
        provider.evaluate.return_value.payload['unverifiable'] = ['No se puede confirmar si van juntos.']
        p, url = self.start(provider=provider)
        description = 'Recipiente azul grande con una marca visible en el borde.'
        self.confirm(p, url, description=description)
        publication = Publication.objects.get()
        final = current_evaluation(publication)
        reviewer = Mock(is_simulated=True, evaluate=Mock(return_value=ProviderResponse(simulated_response())))
        process_evaluation(final.pk, provider=reviewer)
        final.refresh_from_db()
        self.assertEqual(final.status, 'completed')
        self.assertEqual(publication.description, description)
        self.assertEqual(final.origin, 'review')
        self.assertEqual(reviewer.evaluate.call_args.args[0].snapshot['description'], description)
        audit = AuditEvent.objects.get(action='publication.submitted')
        self.assertEqual(audit.after['description'], description)
        self.assertEqual(audit.metadata['ai_prefill']['description'],
                         'Dos recipientes azules de distinto tamaño.')
        self.assertEqual(p.evaluations.get(origin='preanalysis').result['unverifiable'],
                         ['No se puede confirmar si van juntos.'])
        self.assertFalse(EvaluationAttempt.objects.filter(is_simulated=False).exists())
