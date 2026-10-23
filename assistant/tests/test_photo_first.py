from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.urls import reverse

from assistant.fixtures import simulated_response
from assistant.models import EvaluationAttempt, PublicationPreparation
from assistant.preparations import run_preanalysis
from assistant.providers import ProviderResponse, request_content
from assistant.schema import response_json_schema
from assistant.services import current_evaluation, process_evaluation, validate_result
from assistant.tests import test_preanalysis as legacy
from audit.models import AuditEvent
from publications.models import Publication, PublicationPhoto
from publications.tests.test_citizen_views import image_upload


@override_settings(OPENAI_EVALUATION_BACKEND='mock', OPENAI_MODEL='fixture-model')
class PhotoFirstTests(TestCase):
    setUp = legacy.PreanalysisTests.setUp
    form_data = legacy.PreanalysisTests.form_data
    confirm = legacy.PreanalysisTests.confirm

    def provider(self, **prefill):
        payload = simulated_response('suitable')
        payload['prefill'] = {'title': 'Jarrón azul', 'description': 'Jarrón con dibujo de flores.',
                              'category_id': self.category.pk, **prefill}
        payload['suggestions'] = []
        return Mock(is_simulated=True, evaluate=Mock(return_value=ProviderResponse(payload)))

    def start(self, *, provider=None, key=None, target=None, **changes):
        data = {'action': 'analyze_photos', 'photos': image_upload(),
                'preparation_request_key': str(key or uuid4()), **changes}
        url = reverse('publications:edit', args=[target.pk]) if target else reverse('publications:create')
        with patch('assistant.services.get_provider', return_value=provider or self.provider()):
            response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        return PublicationPreparation.objects.latest('created_at'), response.url

    def test_photos_alone_create_private_prefilled_form_without_submission(self):
        provider = self.provider()
        p, url = self.start(provider=provider)
        provider.evaluate.assert_called_once()
        self.assertEqual(p.original_data['title'], '')
        self.assertIsNone(p.original_data['category_id'])
        self.assertEqual(p.working_data['title'], 'Jarrón azul')
        evaluation = p.evaluations.get()
        self.assertEqual(evaluation.snapshot['analysis_mode'], 'photo_first')
        self.assertEqual(evaluation.snapshot['title'], '')
        self.assertFalse(Publication.objects.exists())
        page = self.client.get(url)
        self.assertContains(page, 'value="Jarrón azul"')
        self.assertContains(page, 'Enviar a revisión')
        self.assertContains(page, '>Cancelar</button>')
        self.assertContains(page, 'Valoración orientativa')
        self.assertNotContains(page, 'Vista previa')
        self.assertNotContains(page, 'Aceptar todas')
        self.assertNotContains(page, 'data-review-suggestion=')

    def test_entry_waits_for_explicit_continue_then_analyzes_the_complete_batch_once(self):
        with patch('assistant.services.get_provider') as api:
            response = self.client.get(reverse('publications:create'))
        self.assertContains(response, 'Cuando pulses «Continuar con estas fotografías»')
        api.assert_not_called()
        self.assertFalse(PublicationPreparation.objects.exists())
        provider = self.provider()
        p, url = self.start(provider=provider, photos=[image_upload(f'{i}.jpg') for i in range(3)],
                            primary_photo='new:2')
        provider.evaluate.assert_called_once()
        analyzed = provider.evaluate.call_args.args[1]
        self.assertEqual(len(analyzed), 3)
        self.assertTrue(analyzed[0].is_primary)
        self.assertEqual(p.photos.get(is_primary=True).sort_order, 2)
        self.assertEqual(p.photos.count(), 3)
        self.assertFalse(Publication.objects.exists())
        self.assertContains(self.client.get(url), 'data-photo-analysis-actions')
        self.assertContains(self.client.get(url), 'data-review-confirm')

    def test_uncertain_results_leave_fields_empty_and_require_manual_completion(self):
        p, url = self.start(provider=self.provider(title=None, description=None, category_id=None))
        self.assertEqual(p.working_data['title'], '')
        self.assertIsNone(p.working_data['category_id'])
        response = self.confirm(p, url, category='')
        self.assertIn('title', response.context['form'].errors)
        self.assertIn('description', response.context['form'].errors)
        self.assertIn('category', response.context['form'].errors)
        self.assertFalse(Publication.objects.exists())
        self.confirm(p, url, title='Objeto identificado manualmente', description='Descripción real.',
                     category=self.category.pk)
        self.assertEqual(Publication.objects.get().title, 'Objeto identificado manualmente')

    def test_final_user_data_is_audited_and_evaluated_by_operator_review(self):
        p, url = self.start()
        self.confirm(p, url, title='Mi jarrón', description='Tiene una marca.', category=self.alternative.pk)
        pub = Publication.objects.get()
        self.assertEqual(pub.status, 'pending_review')
        final = current_evaluation(pub)
        self.assertEqual((final.origin, final.status), ('review', 'pending'))
        self.assertEqual(final.snapshot['title'], 'Mi jarrón')
        self.assertEqual(final.snapshot['description'], 'Tiene una marca.')
        provider = Mock(is_simulated=True, evaluate=Mock(return_value=ProviderResponse(simulated_response())))
        process_evaluation(final.pk, provider=provider)
        self.assertEqual(provider.evaluate.call_args.args[0].snapshot['category_id'], self.alternative.pk)
        metadata = AuditEvent.objects.get(action='publication.submitted').metadata
        self.assertEqual(metadata['ai_prefill']['title'], 'Jarrón azul')
        self.assertEqual(metadata['flow'], 'photo_first')
        self.assertFalse(metadata['preanalysis_reused'])

    def test_even_unchanged_prefill_requires_review_of_the_final_text(self):
        p, url = self.start()
        self.confirm(p, url)
        self.assertEqual(current_evaluation(Publication.objects.get()).origin, 'review')

    def test_mobile_publication_context_and_two_stage_progress_are_scoped_to_the_flow(self):
        page = self.client.get(reverse('publications:create'))
        self.assertContains(page, 'body--publication-photos')
        self.assertContains(page, 'body--publication-flow')
        self.assertContains(page, 'publication-flow-legal', count=3)
        self.assertContains(page, 'Añade al menos una fotografía para continuar.')
        self.assertContains(page, 'publication_mobile.css')
        self.assertContains(page, '<li aria-current="step"><span aria-hidden="true">1</span><strong>Fotos</strong></li>', html=True)
        _, url = self.start()
        page = self.client.get(url)
        self.assertContains(page, 'body--publication-flow')
        self.assertContains(page, 'publication-flow-legal', count=3)
        self.assertContains(page, '<li aria-current="step"><span aria-hidden="true">2</span><strong>Revisar y enviar</strong></li>', html=True)
        _, old_url = legacy.PreanalysisTests.start(self, provider=legacy.PreanalysisTests.provider(self))
        page = self.client.get(old_url)
        self.assertContains(page, 'publication_mobile.css')
        self.assertContains(page, 'body--publication-flow')
        self.assertNotContains(page, 'aria-label="Progreso de publicación"')
        page = self.client.get(reverse('profile'))
        self.assertNotContains(page, 'body--publication-flow')
        self.assertNotContains(page, 'publication-flow-legal')

    def test_timeout_allows_manual_submission_with_photos_preserved(self):
        provider = self.provider()
        provider.evaluate.side_effect = TimeoutError
        p, url = self.start(provider=provider)
        self.assertContains(self.client.get(url), 'Completa los datos manualmente')
        self.assertEqual(p.photos.count(), 1)
        self.confirm(p, url, title='Nombre real', description='Datos reales', category=self.category.pk)
        self.assertEqual(Publication.objects.get().status, 'pending_review')
        provider.evaluate.assert_called_once()

    def test_invalid_category_and_malformed_output_fail_safely(self):
        for value in (self.root.pk, 999999):
            with self.subTest(category=value):
                p, url = self.start(provider=self.provider(category_id=value))
                self.assertEqual(p.evaluations.get().error_code, 'invalid_response')
                self.assertIsNone(p.working_data['category_id'])
                self.assertContains(self.client.get(url), 'Completa los datos manualmente')

    def test_reload_duplicate_upload_and_same_photo_reanalysis_do_not_call_again(self):
        provider = self.provider()
        key = uuid4()
        p, url = self.start(provider=provider, key=key)
        same, _ = self.start(provider=provider, key=key)
        self.assertEqual(p.pk, same.pk)
        provider.evaluate.assert_called_once()
        with patch('assistant.services.get_provider') as api:
            self.client.get(url)
            self.client.post(url, self.form_data(p, action='analyze_photos'))
        api.assert_not_called()
        self.assertEqual(p.evaluations.count(), 1)

    def test_new_photos_refill_ai_fields_but_preserve_manual_text_and_cleared_field(self):
        p, url = self.start()
        provider = self.provider(title='Nuevo nombre IA', description='Nueva descripción IA', category_id=self.alternative.pk)
        with patch('assistant.services.get_provider', return_value=provider):
            response = self.client.post(url, self.form_data(p, action='analyze_photos',
                title='Nombre elegido por mí', description='', photos=image_upload('extra.jpg')))
        self.assertEqual(response.status_code, 302)
        provider.evaluate.assert_called_once()
        p.refresh_from_db()
        self.assertEqual(p.working_data['title'], 'Nombre elegido por mí')
        self.assertEqual(p.working_data['description'], '')
        self.assertEqual(p.working_data['category_id'], self.alternative.pk)
        self.assertEqual(p.photos.count(), 2)

    def test_explicit_manual_marker_preserves_a_field_even_if_value_matches_ai(self):
        p, url = self.start()
        with patch('assistant.services.get_provider', return_value=self.provider(title='Otro título IA')):
            self.client.post(url, self.form_data(p, action='analyze_photos', manual_fields=['title'],
                                                photos=image_upload('extra.jpg')))
        p.refresh_from_db()
        self.assertEqual(p.working_data['title'], 'Jarrón azul')

    def test_old_analysis_is_not_applied_again_on_get_or_unchanged_retry(self):
        p, url = self.start()
        self.client.post(url, self.form_data(p, action='analyze_photos', title='Edición manual'))
        self.client.get(url)
        p.refresh_from_db()
        self.assertEqual(p.working_data['title'], 'Edición manual')
        self.assertEqual(p.evaluations.count(), 1)

    def test_existing_draft_data_is_preserved_and_cancel_keeps_original_files(self):
        draft = Publication.objects.create(submitter=self.user, category=self.category,
                                           title='Texto del borrador', description='Descripción anterior')
        photo = PublicationPhoto.objects.create(publication=draft, image=image_upload())
        p, url = self.start(target=draft, photos=[])
        self.assertEqual(p.working_data['title'], draft.title)
        self.assertEqual(p.working_data['description'], draft.description)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {'action': 'cancel'})
        draft.refresh_from_db()
        self.assertEqual((draft.title, draft.status), ('Texto del borrador', 'draft'))
        self.assertTrue(photo.image.storage.exists(photo.image.name))
        self.assertFalse(p.photos.exists())

    def test_upload_validation_rejects_empty_invalid_and_excess_photos_before_ai(self):
        for uploads in ([], ['not-a-file'], [image_upload(f'{i}.jpg') for i in range(9)]):
            with self.subTest(uploads=len(uploads)), patch('assistant.services.get_provider') as api:
                response = self.client.post(reverse('publications:create'), {'action': 'analyze_photos', 'photos': uploads})
                self.assertEqual(response.status_code, 200)
                self.assertIn('photos', response.context['form'].errors)
                api.assert_not_called()

    def test_no_javascript_photo_change_is_analyzed_before_final_confirmation(self):
        p, url = self.start()
        provider = self.provider()
        with patch('assistant.services.get_provider', return_value=provider):
            response = self.confirm(p, url, photos=image_upload('extra.jpg'))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Publication.objects.exists())
        provider.evaluate.assert_called_once()
        self.confirm(p, url)
        self.assertEqual(Publication.objects.get().photos.count(), 2)

    def test_suggestion_actions_cannot_override_directly_edited_fields(self):
        p, url = self.start()
        for action in ('confirm_all', 'confirm_none', 'accept_applicable', 'preview', 'retry'):
            with self.subTest(action=action):
                response = self.client.post(url, self.form_data(p, action=action))
                self.assertContains(response, 'Selecciona una acción válida')
        self.assertFalse(Publication.objects.exists())

    def test_photo_contract_is_separate_from_historical_review_schema(self):
        self.assertIn('prefill', response_json_schema(photo_first=True)['properties'])
        self.assertNotIn('prefill', response_json_schema()['properties'])
        p, _ = self.start()
        payload = self.provider().evaluate.return_value.payload
        payload['suggestions'] = [{'id': 'bad', 'kind': 'replace', 'field': 'title',
                                  'proposed_text': 'Otro', 'category_id': None, 'reason': 'Cambio.'}]
        with self.assertRaises(ValueError):
            validate_result(payload, p.evaluations.get())
        content = request_content(p.evaluations.get(), [])
        self.assertIn('photo_first', content[0]['text'])
        self.assertNotIn('Jarrón azul', content[0]['text'])

    def test_processing_and_stale_photo_forms_are_rejected(self):
        p, url = self.start()
        stale = self.form_data(p, action='analyze_photos')
        self.client.post(url, self.form_data(p, action='analyze_photos', title='Nuevo'))
        self.assertContains(self.client.post(url, stale), 'cambió en otra pestaña')
        p.evaluations.update(status='processing')
        response = self.client.post(url, self.form_data(p, action='analyze_photos'))
        self.assertContains(response, 'Espera a que termine')
        self.assertEqual(p.photos.count(), 1)

    def test_ai_values_and_guidance_are_escaped(self):
        provider = self.provider(title='<script>bad()</script>')
        provider.evaluate.return_value.payload['recommendations'] = ['<img src=x onerror=bad()>']
        p, url = self.start(provider=provider)
        response = self.client.get(url)
        self.assertContains(response, '&lt;script&gt;bad()&lt;/script&gt;')
        self.assertContains(response, '&lt;img src=x onerror=bad()&gt;')
        self.assertNotContains(response, '<script>bad()</script>')

    def test_new_primary_and_photo_deletion_survive_reanalysis_and_submission(self):
        p, url = self.start()
        old = p.photos.get()
        with patch('assistant.services.get_provider', return_value=self.provider()):
            self.client.post(url, self.form_data(p, action='analyze_photos',
                photos=[image_upload('one.jpg'), image_upload('two.jpg')], primary_photo='new:1',
                remove_photos=[old.pk]))
        p.refresh_from_db()
        primary = p.photos.get(is_primary=True)
        self.assertEqual(primary.sort_order, 1)
        self.assertEqual(p.photos.count(), 2)
        self.confirm(p, url)
        pub = Publication.objects.get()
        self.assertEqual(pub.photos.get(is_primary=True).image.name, primary.image.name)
        self.assertEqual(pub.photos.count(), 2)

    @override_settings(OPENAI_EVALUATION_BACKEND='live', OPENAI_LIVE_ENABLED=True, OPENAI_API_KEY='fake')
    def test_live_sdk_remains_blocked_in_photo_first_tests(self):
        with patch('httpx.AsyncClient.send') as transport:
            response = self.client.post(reverse('publications:create'), {
                'action': 'analyze_photos', 'photos': image_upload()})
        self.assertEqual(response.status_code, 302)
        transport.assert_not_called()
        self.assertEqual(EvaluationAttempt.objects.count(), 0)
