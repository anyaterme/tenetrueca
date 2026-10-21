import asyncio
import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.utils import timezone

from assistant.fixtures import simulated_response
from assistant.models import EvaluationAttempt, OpenAIUsageDay, PublicationEvaluation, PublicationPreparation
from assistant.preparations import digest, run_preanalysis, confirm_preparation
from assistant.providers import OpenAIProvider, ProviderResponse
from assistant.services import current_evaluation, process_evaluation, validate_result, claim_evaluation
from audit.models import AuditEvent
from catalog.models import Category
from publications.models import Publication, PublicationPhoto
from publications.tests.test_citizen_views import image_upload


@override_settings(OPENAI_EVALUATION_BACKEND='mock', OPENAI_MODEL='fixture-model')
class PreanalysisTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.media = Path(media.name)
        self.addCleanup(media.cleanup)
        override = override_settings(PRIVATE_MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.user = get_user_model().objects.create_user(username='pre-citizen', email='pre@example.test')
        self.other = get_user_model().objects.create_user(username='pre-other', email='other@example.test')
        self.root = Category.objects.create(name='Hogar', slug='pre-hogar')
        self.category = Category.objects.create(name='Decoración', slug='pre-decoracion', parent=self.root)
        self.alternative = Category.objects.create(name='Menaje', slug='pre-menaje', parent=self.root)
        self.client.force_login(self.user)

    def provider(self, suggestions=None):
        payload = simulated_response('suitable')
        payload['suggestions'] = suggestions if suggestions is not None else [
            {'id': 'fix_title', 'field': 'title', 'kind': 'replace',
             'proposed_text': 'Jarrón de cerámica', 'category_id': None, 'reason': 'Aclara el nombre.'},
            {'id': 'fix_description', 'field': 'description', 'kind': 'replace',
             'proposed_text': 'Jarrón con dibujo de flores.', 'category_id': None, 'reason': 'Describe el dibujo.'},
        ]
        return Mock(is_simulated=True, evaluate=Mock(return_value=ProviderResponse(payload)))

    def start(self, *, provider=None, key=None, target=None, **changes):
        data = {'title': 'Un objeto', 'description': 'Descripción original del objeto.',
                'parent_category': self.root.pk, 'category': self.category.pk, 'action': 'submit',
                'photos': image_upload(), 'preparation_request_key': str(key or uuid4())}
        data.update(changes)
        url = reverse('publications:edit', args=[target.pk]) if target else reverse('publications:create')
        with patch('assistant.services.get_provider', return_value=provider or self.provider()):
            response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        preparation = PublicationPreparation.objects.latest('created_at')
        return preparation, response.url

    def form_data(self, preparation, **changes):
        preparation.refresh_from_db()
        data = {'action': 'confirm_selected', 'version': digest(preparation.working_data),
                'title': preparation.working_data['title'],
                'description': preparation.working_data['description'],
                'category': preparation.working_data['category_id']}
        evaluation = preparation.evaluations.first()
        for suggestion in evaluation.result.get('suggestions', []):
            data[f'suggestion_{suggestion["id"]}'] = suggestion['category_id'] if suggestion['field'] == 'category' else suggestion['proposed_text'] or ''
        data.update(changes)
        return data

    def confirm(self, preparation, url, **changes):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, self.form_data(preparation, **changes))

    def test_preanalysis_finishes_in_request_without_creating_publication_or_queue_job(self):
        provider = self.provider()
        preparation, url = self.start(provider=provider)
        provider.evaluate.assert_called_once()
        evaluation = preparation.evaluations.get()
        self.assertEqual((evaluation.origin, evaluation.status), ('preanalysis', 'completed'))
        self.assertIsNone(evaluation.publication_id)
        self.assertFalse(Publication.objects.exists())
        self.assertFalse(PublicationEvaluation.objects.filter(origin='review').exists())
        self.assertLessEqual(provider.evaluate.call_args.kwargs['timeout_seconds'], 60)
        self.assertContains(self.client.get(url), 'Aceptar todas las aplicables')
        self.assertContains(self.client.get(url), 'Simulación de desarrollo')
        self.assertEqual(OpenAIUsageDay.objects.count(), 0)

    def test_no_changes_reuses_preanalysis_and_worker_makes_no_second_call(self):
        preparation, url = self.start()
        response = self.confirm(preparation, url, action='confirm_none')
        publication = Publication.objects.get()
        self.assertRedirects(response, reverse('publications:detail', args=[publication.pk]))
        self.assertEqual(publication.title, 'Un objeto')
        self.assertEqual(publication.status, 'pending_review')
        self.assertEqual(current_evaluation(publication).pk, preparation.evaluations.get().pk)
        with patch('assistant.services.get_provider') as provider:
            call_command('evaluation_worker', once=True, stdout=io.StringIO())
        provider.assert_not_called()
        self.assertEqual(EvaluationAttempt.objects.count(), 1)

    def test_invalid_dimensions_explain_failed_analysis_without_claiming_no_suggestions(self):
        provider = self.provider()
        provider.evaluate.return_value.payload['dimensions']['method'] = 'unverifiable'
        preparation, url = self.start(provider=provider)
        evaluation = preparation.evaluations.get()
        self.assertEqual((evaluation.status, evaluation.error_code), ('error', 'invalid_response'))
        response = self.client.get(url)
        self.assertContains(response, 'No hemos podido revisar tu objeto con la IA')
        self.assertContains(response, 'Puedes enviarlos a revisión humana o reintentar el análisis.')
        self.assertContains(response, 'Reintentar análisis')
        self.assertContains(response, 'Cancelar envío')
        self.assertNotContains(response, 'La IA no propone cambios concretos')
        self.assertContains(response, 'Enviar a revisión')
        self.assertFalse(Publication.objects.exists())
        self.assertEqual(preparation.photos.count(), 1)

    def test_completed_analysis_without_suggestions_can_still_be_submitted(self):
        preparation, url = self.start(provider=self.provider([]))
        response = self.client.get(url)
        self.assertContains(response, 'El análisis ha finalizado sin sugerencias de cambios.')
        self.assertContains(response, 'Enviar a revisión')
        self.assertNotContains(response, 'Las sugerencias estarán disponibles cuando se complete el análisis.')

    def test_accept_all_saves_proposed_values_and_schedules_final_analysis(self):
        preparation, url = self.start()
        response = self.confirm(preparation, url, action='confirm_all')
        self.assertEqual(response.status_code, 302)
        publication = Publication.objects.get()
        self.assertEqual(publication.title, 'Jarrón de cerámica')
        self.assertEqual(publication.description, 'Jarrón con dibujo de flores.')
        evaluation = current_evaluation(publication)
        self.assertEqual((evaluation.origin, evaluation.status), ('review', 'pending'))
        self.assertEqual(evaluation.snapshot['title'], publication.title)
        provider = self.provider([])
        process_evaluation(evaluation.pk, provider=provider)
        self.assertEqual(provider.evaluate.call_args.args[0].snapshot['description'], publication.description)
        self.assertEqual(EvaluationAttempt.objects.count(), 2)
        preparation.refresh_from_db()
        self.assertEqual(set(preparation.selected_suggestions), {'fix_title', 'fix_description'})
        self.assertEqual(preparation.original_data['title'], 'Un objeto')

    def test_partial_acceptance_changes_only_selected_field_and_records_choice(self):
        preparation, url = self.start()
        self.confirm(preparation, url, accepted_suggestions=['fix_title'])
        publication = Publication.objects.get()
        self.assertEqual(publication.title, 'Jarrón de cerámica')
        self.assertEqual(publication.description, preparation.original_data['description'])
        event = AuditEvent.objects.get(action='publication.submitted')
        self.assertEqual(event.metadata['accepted_suggestions'], ['fix_title'])
        self.assertFalse(event.metadata['preanalysis_reused'])

    def test_cancel_cleans_files_without_submission_or_changes_to_existing_draft(self):
        draft = Publication.objects.create(submitter=self.user, title='Borrador anterior',
                                           description='Texto anterior', category=self.category)
        original = PublicationPhoto.objects.create(publication=draft, image=image_upload())
        preparation, url = self.start(target=draft)
        staged = list(preparation.photos.all())
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {'action': 'cancel'})
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual((draft.title, draft.status), ('Borrador anterior', 'draft'))
        self.assertTrue(original.image.storage.exists(original.image.name))
        self.assertFalse(preparation.photos.exists())
        self.assertTrue(all(not photo.image.storage.exists(photo.image.name) for photo in staged))
        self.assertFalse(AuditEvent.objects.filter(action='publication.submitted').exists())

    def test_expiration_cleans_staging_and_keeps_confirmed_photos(self):
        preparation, url = self.start()
        self.confirm(preparation, url, action='confirm_none')
        photo = PublicationPhoto.objects.get()
        preparation.expires_at = timezone.now() - timedelta(seconds=1)
        preparation.save(update_fields=['expires_at'])
        with self.captureOnCommitCallbacks(execute=True):
            call_command('expire_preparations', stdout=io.StringIO())
        self.assertFalse(preparation.photos.exists())
        self.assertTrue(photo.image.storage.exists(photo.image.name))
        self.assertIsNotNone(current_evaluation(Publication.objects.get()))

    def test_expired_preparation_cannot_be_confirmed(self):
        preparation, url = self.start()
        data = self.form_data(preparation, action='confirm_none')
        preparation.expires_at = timezone.now() - timedelta(seconds=1)
        preparation.save(update_fields=['expires_at'])
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertFalse(Publication.objects.exists())

    def test_private_preparation_and_photos_require_owner_even_for_other_citizen(self):
        preparation, url = self.start()
        photo_url = reverse('publications:preparation_photo', args=[preparation.pk, preparation.photos.get().pk])
        response = self.client.get(photo_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        # Exhaust the test client's streaming wrapper so it closes the file without
        # firing request_finished against TestCase's active PostgreSQL transaction.
        self.assertTrue(b''.join(response.streaming_content))
        self.assertTrue(response.closed)
        self.client.force_login(self.other)
        for endpoint in (url, photo_url):
            self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(self.client.post(url, {'action': 'cancel'}).status_code, 404)

    def test_duplicate_initial_request_calls_provider_once(self):
        key = uuid4()
        provider = self.provider()
        first, url = self.start(provider=provider, key=key)
        second, second_url = self.start(provider=provider, key=key)
        self.assertEqual((first.pk, url), (second.pk, second_url))
        provider.evaluate.assert_called_once()
        self.assertEqual(PublicationPreparation.objects.count(), 1)

    def test_double_confirmation_is_idempotent(self):
        preparation, url = self.start()
        data = self.form_data(preparation, action='confirm_none')
        first = self.client.post(url, data)
        second = self.client.post(url, data)
        self.assertEqual(first.url, second.url)
        self.assertEqual(Publication.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='publication.submitted').count(), 1)

    def test_same_request_key_with_changed_content_is_rejected(self):
        key = uuid4()
        self.start(key=key)
        response = self.client.post(reverse('publications:create'), {
            'title': 'Otro', 'description': 'Otra descripción', 'parent_category': self.root.pk,
            'category': self.category.pk, 'action': 'submit', 'photos': image_upload(),
            'preparation_request_key': str(key),
        })
        self.assertContains(response, 'ya se utilizó con otros datos')
        self.assertEqual(EvaluationAttempt.objects.count(), 1)

    def test_manual_changes_without_accepting_suggestions_require_new_analysis(self):
        preparation, url = self.start()
        self.confirm(preparation, url, action='confirm_none', title='Mi título manual')
        publication = Publication.objects.get()
        self.assertEqual(publication.title, 'Mi título manual')
        self.assertEqual(current_evaluation(publication).origin, 'review')

    def test_category_or_configuration_changes_prevent_reuse(self):
        preparation, url = self.start()
        with override_settings(AI_IMAGE_DETAIL='low'):
            self.confirm(preparation, url, action='confirm_none')
        self.assertEqual(Publication.objects.get().ai_evaluations.count(), 2)

    def test_photo_changes_prevent_reuse(self):
        preparation, url = self.start()
        self.confirm(preparation, url, action='confirm_none', photos=image_upload('extra.jpg'))
        publication = Publication.objects.get()
        self.assertEqual(publication.photos.count(), 2)
        self.assertEqual(current_evaluation(publication).origin, 'review')

    def test_preanalysis_timeout_preserves_data_and_does_not_retry_automatically(self):
        provider = self.provider()
        provider.evaluate.side_effect = TimeoutError
        preparation, url = self.start(provider=provider)
        evaluation = preparation.evaluations.get()
        self.assertEqual((evaluation.status, evaluation.error_code), ('error', 'timeout_unknown_usage'))
        self.assertContains(self.client.get(url), '60 segundos')
        self.assertContains(self.client.get(url), 'Descripción original del objeto.')
        self.assertEqual(run_preanalysis(preparation.pk, provider=provider).pk, evaluation.pk)
        provider.evaluate.assert_called_once()
        self.assertFalse(Publication.objects.exists())
        self.assertEqual(preparation.photos.count(), 1)

    @override_settings(AI_PREANALYSIS_TIMEOUT_SECONDS=12)
    def test_timeout_configuration_is_used_by_server_and_dialog(self):
        provider = self.provider()
        self.start(provider=provider)
        self.assertLessEqual(provider.evaluate.call_args.kwargs['timeout_seconds'], 12)
        response = self.client.get(reverse('publications:create'))
        self.assertContains(response, 'data-ai-timeout="12"')
        self.assertContains(response, '12 segundos')

    def test_explicit_retry_analyzes_corrected_data_once_and_worker_ignores_preanalysis(self):
        provider = self.provider()
        provider.evaluate.side_effect = TimeoutError
        preparation, url = self.start(provider=provider)
        retry_provider = self.provider()
        with patch('assistant.services.get_provider', return_value=retry_provider):
            response = self.client.post(url, self.form_data(preparation, action='retry', title='Título corregido'))
        self.assertEqual(response.status_code, 302)
        retry_provider.evaluate.assert_called_once()
        self.assertEqual(preparation.evaluations.first().snapshot['title'], 'Título corregido')
        with patch('assistant.services.get_provider') as provider:
            call_command('evaluation_worker', once=True, stdout=io.StringIO())
        provider.assert_not_called()
        self.assertFalse(Publication.objects.exists())

    def test_processing_preanalysis_cannot_be_used_to_submit(self):
        preparation, url = self.start()
        preparation.evaluations.update(status='processing')
        response = self.client.post(url, self.form_data(preparation, action='confirm_selected'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Publication.objects.exists())

    def test_failed_preanalysis_can_be_sent_to_human_review_with_a_fresh_final_job(self):
        provider = self.provider()
        provider.evaluate.side_effect = TimeoutError
        preparation, url = self.start(provider=provider)
        failed = preparation.evaluations.get()
        with self.captureOnCommitCallbacks(execute=True), patch('assistant.services.get_provider') as api:
            response = self.client.post(url, self.form_data(
                preparation, action='confirm_selected', title='Nombre corregido manualmente'))
        self.assertEqual(response.status_code, 302)
        api.assert_not_called()
        publication = Publication.objects.get()
        self.assertEqual((publication.status, publication.title), ('pending_review', 'Nombre corregido manualmente'))
        failed.refresh_from_db()
        self.assertIsNone(failed.publication_id)
        final = current_evaluation(publication)
        self.assertEqual((final.origin, final.status), ('review', 'pending'))
        event = AuditEvent.objects.get(action='publication.submitted')
        self.assertEqual(event.metadata['preanalysis_status'], 'error')
        self.assertEqual(event.metadata['preanalysis_error_code'], 'timeout_unknown_usage')
        self.assertFalse(event.metadata['preanalysis_reused'])
        self.assertEqual(event.metadata['accepted_suggestions'], [])
        self.assertFalse(publication.moderation_decisions.exists())

    def test_existing_publication_changed_in_another_tab_is_not_overwritten(self):
        draft = Publication.objects.create(submitter=self.user, category=self.category, title='Antes', description='Antes')
        preparation, url = self.start(target=draft)
        draft.title = 'Editado en otra pestaña'
        draft.save()
        self.confirm(preparation, url, action='confirm_none')
        draft.refresh_from_db()
        self.assertEqual(draft.title, 'Editado en otra pestaña')
        self.assertEqual(draft.status, 'draft')

    def test_stale_review_form_cannot_overwrite_newer_selection(self):
        preparation, url = self.start()
        stale = self.form_data(preparation, action='confirm_none')
        self.client.post(url, self.form_data(preparation, action='preview', title='Edición nueva'))
        response = self.client.post(url, stale)
        self.assertContains(response, 'cambió en otra pestaña')
        self.assertFalse(Publication.objects.exists())

    def test_information_suggestion_needs_user_input_and_is_appended_after_replacement(self):
        information = {'id': 'state', 'field': 'description', 'kind': 'information',
                       'proposed_text': None, 'category_id': None, 'reason': 'Indica el estado real.'}
        suggestions = [information] + self.provider().evaluate.return_value.payload['suggestions']
        preparation, url = self.start(provider=self.provider(suggestions))
        response = self.confirm(preparation, url, action='confirm_all')
        self.assertContains(response, 'Completa este campo')
        self.assertEqual(set(response.context['form']['accepted_suggestions'].value()),
                         {'state', 'fix_title', 'fix_description'})
        self.assertFalse(Publication.objects.exists())
        self.confirm(preparation, url, action='confirm_all', suggestion_state='Tiene una pequeña marca.')
        self.assertEqual(Publication.objects.get().description, 'Jarrón con dibujo de flores.\n\nTiene una pequeña marca.')

    def test_photo_suggestion_requires_an_actual_new_photo(self):
        suggestion = {'id': 'back_photo', 'field': 'photos', 'kind': 'photo', 'proposed_text': None,
                      'category_id': None, 'reason': 'Añade una foto trasera.'}
        preparation, url = self.start(provider=self.provider([suggestion]))
        self.assertContains(self.confirm(preparation, url, action='confirm_all'), 'Añade una fotografía')
        self.assertFalse(Publication.objects.exists())
        self.confirm(preparation, url, action='confirm_all', photos=image_upload('back.jpg'))
        self.assertEqual(PublicationPhoto.objects.count(), 2)

    def test_preview_does_not_register_submission_and_does_not_duplicate_added_information(self):
        suggestion = {'id': 'state', 'field': 'description', 'kind': 'information',
                      'proposed_text': None, 'category_id': None, 'reason': 'Describe el estado.'}
        preparation, url = self.start(provider=self.provider([suggestion]))
        response = self.confirm(preparation, url, action='preview', accepted_suggestions=['state'], suggestion_state='Una marca.')
        self.assertContains(response, 'Vista previa del envío')
        self.assertFalse(Publication.objects.exists())
        self.confirm(preparation, url, accepted_suggestions=['state'], suggestion_state='Una marca.')
        self.assertEqual(Publication.objects.get().description.count('Una marca.'), 1)

    def test_user_can_ignore_suggestions_for_prohibited_item_and_operator_keeps_findings(self):
        provider = self.provider([])
        provider.evaluate.return_value = ProviderResponse(simulated_response('prohibited'))
        preparation, url = self.start(provider=provider)
        self.confirm(preparation, url, action='confirm_none')
        publication = Publication.objects.get()
        self.assertEqual(publication.status, 'pending_review')
        self.assertEqual(current_evaluation(publication).result['checks']['prohibited_items']['status'], 'INCIDENCIA')

    def test_category_suggestion_is_selected_using_active_catalog(self):
        suggestion = {'id': 'classification', 'field': 'category', 'kind': 'replace',
                      'proposed_text': None, 'category_id': self.alternative.pk, 'reason': 'Otra clasificación.'}
        preparation, url = self.start(provider=self.provider([suggestion]))
        self.confirm(preparation, url, action='confirm_all')
        self.assertEqual(Publication.objects.get().category_id, self.alternative.pk)

    def test_suggestions_reject_duplicate_ids_fields_and_unavailable_categories(self):
        preparation, _ = self.start()
        evaluation = preparation.evaluations.get()
        original = self.provider().evaluate.return_value.payload
        for suggestions in ([original['suggestions'][0]] * 2,
                            [original['suggestions'][0], {**original['suggestions'][0], 'id': 'other'}],
                            [{'id': 'category', 'field': 'category', 'kind': 'replace', 'proposed_text': None,
                              'category_id': 999999, 'reason': 'Inválida.'}]):
            with self.subTest(suggestions=suggestions), self.assertRaises(ValueError):
                validate_result({**original, 'suggestions': suggestions}, evaluation)

    def test_bulk_acceptance_only_selects_applicable_replacements(self):
        suggestions = self.provider().evaluate.return_value.payload['suggestions'] + [
            {'id': 'details', 'field': 'description', 'kind': 'information', 'proposed_text': None,
             'category_id': None, 'reason': 'Describe el estado.'},
            {'id': 'photo', 'field': 'photos', 'kind': 'photo', 'proposed_text': None,
             'category_id': None, 'reason': 'Añade una foto.'},
        ]
        preparation, url = self.start(provider=self.provider(suggestions))
        response = self.client.post(url, self.form_data(preparation, action='accept_applicable'))
        self.assertEqual(response.status_code, 200)
        preparation.refresh_from_db()
        self.assertEqual(preparation.selected_suggestions, ['fix_description'])
        self.assertFalse(Publication.objects.exists())
        self.assertIsNone(response.context['preview'])
        self.assertEqual(EvaluationAttempt.objects.count(), 1)

    def test_edited_accepted_and_discarded_suggestions_survive_preview_and_submission(self):
        preparation, url = self.start()
        response = self.client.post(url, self.form_data(
            preparation, action='preview', accepted_suggestions=['fix_title'],
            discarded_suggestions=['fix_description'], suggestion_fix_title='Mi jarrón azul',
            description='Mi descripción corregida manualmente.'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['preview']['title'], 'Mi jarrón azul')
        self.assertEqual(response.context['preview']['description'], 'Mi descripción corregida manualmente.')
        rows = {row['id']: row for row in response.context['suggestion_rows']}
        self.assertTrue(rows['fix_title']['modified'])
        self.assertTrue(rows['fix_description']['discarded'])
        preparation.refresh_from_db()
        self.assertEqual(preparation.working_data['discarded_suggestions'], ['fix_description'])
        self.confirm(preparation, url, accepted_suggestions=['fix_title'],
                     discarded_suggestions=['fix_description'], suggestion_fix_title='Mi jarrón azul',
                     description='Mi descripción corregida manualmente.')
        publication = Publication.objects.get()
        self.assertEqual(publication.title, 'Mi jarrón azul')
        self.assertEqual(publication.description, 'Mi descripción corregida manualmente.')
        self.assertEqual(AuditEvent.objects.get(action='publication.submitted').metadata[
            'discarded_suggestions'], ['fix_description'])

    def test_newly_added_photo_can_be_selected_as_primary(self):
        preparation, url = self.start()
        self.confirm(preparation, url, action='confirm_none',
                     photos=[image_upload('new1.jpg'), image_upload('new2.jpg')], primary_photo='new:1')
        publication = Publication.objects.get()
        photos = list(publication.photos.order_by('sort_order'))
        self.assertEqual(len(photos), 3)
        self.assertEqual([p.is_primary for p in photos], [False, False, True])

    def test_removed_photo_cannot_fulfil_an_accepted_photo_suggestion(self):
        suggestion = {'id': 'photo', 'field': 'photos', 'kind': 'photo', 'proposed_text': None,
                      'category_id': None, 'reason': 'Añade una fotografía.'}
        preparation, url = self.start(provider=self.provider([suggestion]))
        self.client.post(url, self.form_data(preparation, action='preview', photos=image_upload('extra.jpg')))
        preparation.refresh_from_db()
        added = preparation.working_data['added_photos']
        response = self.confirm(preparation, url, accepted_suggestions=['photo'], remove_photos=added)
        self.assertEqual(response.status_code, 200)
        self.assertIn('photos', response.context['form'].errors)
        self.assertFalse(Publication.objects.exists())
        self.assertEqual(preparation.photos.count(), 2)

    def test_retry_reuses_completed_analysis_for_identical_content(self):
        preparation, url = self.start()
        first = preparation.evaluations.get()
        with patch('assistant.services.get_provider') as provider:
            response = self.client.post(url, self.form_data(preparation, action='retry'))
        provider.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(preparation.evaluations.count(), 1)
        self.assertEqual(preparation.evaluations.get().pk, first.pk)

    def test_explicit_retry_of_changed_completed_content_analyzes_it_once(self):
        preparation, url = self.start()
        provider = self.provider([])
        with patch('assistant.services.get_provider', return_value=provider):
            response = self.client.post(url, self.form_data(preparation, action='retry', title='Otro nombre'))
        self.assertEqual(response.status_code, 302)
        provider.evaluate.assert_called_once()
        self.assertEqual(preparation.evaluations.count(), 2)
        self.assertEqual(preparation.evaluations.first().snapshot['title'], 'Otro nombre')

    def test_failed_analysis_fallback_still_requires_valid_category_and_a_photo(self):
        provider = self.provider()
        provider.evaluate.side_effect = TimeoutError
        preparation, url = self.start(provider=provider)
        photo = preparation.photos.get()
        response = self.confirm(preparation, url, category=self.root.pk, remove_photos=[photo.pk])
        self.assertEqual(response.status_code, 200)
        self.assertIn('category', response.context['form'].errors)
        self.assertIn('photos', response.context['form'].errors)
        self.assertFalse(Publication.objects.exists())
        self.assertTrue(photo.image.storage.exists(photo.image.name))

    def test_ai_text_is_escaped_and_never_executed(self):
        provider = self.provider()
        provider.evaluate.return_value.payload['suggestions'][0]['reason'] = '<script>alert(1)</script>'
        preparation, url = self.start(provider=provider)
        response = self.client.get(url)
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_csrf_is_required_for_preanalysis_confirmation_and_cancel(self):
        preparation, url = self.start()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('publications:create'), {}).status_code, 403)
        self.assertEqual(client.post(url, {'action': 'cancel'}).status_code, 403)
        self.assertEqual(client.post(url, self.form_data(preparation)).status_code, 403)

    @override_settings(OPENAI_EVALUATION_BACKEND='live', OPENAI_LIVE_ENABLED=True, OPENAI_API_KEY='fake')
    def test_preanalysis_cannot_call_live_sdk_in_tests_even_with_key(self):
        data = {'title': 'Objeto', 'description': 'Descripción', 'parent_category': self.root.pk,
                'category': self.category.pk, 'action': 'submit', 'photos': image_upload()}
        with patch('httpx.AsyncClient.send') as transport:
            response = self.client.post(reverse('publications:create'), data)
        self.assertEqual(response.status_code, 302)
        transport.assert_not_called()
        self.assertEqual(PublicationEvaluation.objects.get().error_code, 'live_disabled')
        self.assertFalse(Publication.objects.exists())

    @override_settings(AI_IMAGE_MAX_DIMENSION='invalid')
    def test_invalid_image_configuration_keeps_data_and_reports_analysis_failure(self):
        preparation, url = self.start()
        self.assertEqual(preparation.evaluations.get().status, 'error')
        self.assertContains(self.client.get(url), 'No hemos podido revisar tu objeto con la IA')

    def test_synchronous_provider_deadline_cancels_wait_without_background_request(self):
        cancelled = []

        async def slow_response(**kwargs):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)

        factory = MagicMock()
        client = factory.return_value.__aenter__.return_value
        client.responses.create = AsyncMock(side_effect=slow_response)
        with patch('openai.AsyncOpenAI', factory), self.assertRaises(TimeoutError):
            asyncio.run(OpenAIProvider()._with_deadline({}, .01))
        self.assertEqual(cancelled, [True])
        self.assertEqual(factory.call_args.kwargs['max_retries'], 0)

    def test_late_result_for_changed_preparation_is_obsolete(self):
        provider = self.provider()

        def change_while_analyzing(evaluation, *args, **kwargs):
            preparation = evaluation.preparation
            preparation.working_data = {**preparation.working_data, 'title': 'Modificado durante el análisis'}
            preparation.save(update_fields=['working_data'])
            return ProviderResponse(simulated_response('suitable'))

        provider.evaluate.side_effect = change_while_analyzing
        preparation, url = self.start(provider=provider)
        self.assertEqual(preparation.evaluations.get().status, 'obsolete')
        self.assertContains(self.client.get(url), 'Reintentar análisis')
        self.assertFalse(Publication.objects.exists())

    def test_confirm_rejects_changes_between_form_save_and_final_transaction(self):
        preparation, _ = self.start()
        expected = digest(preparation.working_data)
        preparation.working_data = {**preparation.working_data, 'title': 'Otra edición'}
        preparation.save(update_fields=['working_data'])
        with self.assertRaisesMessage(ValidationError, 'cambió antes de confirmar'):
            confirm_preparation(preparation.pk, self.user, preparation.original_data, expected_version=expected)
        self.assertFalse(Publication.objects.exists())

    def test_daily_limit_is_checked_again_at_confirmation(self):
        preparation, url = self.start()
        with override_settings(PUBLICATION_DAILY_LIMIT=0):
            response = self.confirm(preparation, url, action='confirm_none')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Publication.objects.exists())
        preparation.refresh_from_db()
        self.assertEqual(preparation.status, 'open')

    def test_old_photos_without_metadata_can_be_preanalyzed_without_modifying_original(self):
        draft = Publication.objects.create(submitter=self.user, title='Anterior',
                                           description='Anterior', category=self.category)
        photo = PublicationPhoto.objects.create(publication=draft, image=image_upload())
        original_name = photo.image.name
        PublicationPhoto.objects.filter(pk=photo.pk).update(image_format='', width=None, height=None)
        preparation, _ = self.start(target=draft, photos=[])
        self.assertEqual(preparation.photos.get().image_format, 'JPEG')
        photo.refresh_from_db()
        self.assertEqual(photo.image.name, original_name)
        self.assertIsNone(photo.width)

    def test_direct_worker_claim_ignores_preanalysis(self):
        preparation, _ = self.start()
        self.assertIsNone(claim_evaluation(preparation.evaluations.get().pk))

    def test_interrupted_preanalysis_can_be_explicitly_retried_after_deadline(self):
        preparation, url = self.start()
        evaluation = preparation.evaluations.get()
        preparation.evaluations.update(status='processing', started_at=timezone.now() - timedelta(seconds=100))
        self.assertContains(self.client.get(url), 'Reintentar análisis')
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.error_code, 'worker_interrupted_unknown_usage')

    def test_ajax_preanalysis_returns_destination_without_creating_publication(self):
        response = self.client.post(reverse('publications:create'), {
            'title': 'Objeto', 'description': 'Descripción', 'parent_category': self.root.pk,
            'category': self.category.pk, 'action': 'submit', 'photos': image_upload(),
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertIn('/preparacion/', response.json()['url'])
        self.assertFalse(Publication.objects.exists())
