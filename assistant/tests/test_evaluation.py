import asyncio
import base64
import io
import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import httpx
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAI, RateLimitError
from PIL import Image

from accounts.models import UserCenterAccess
from assistant.fixtures import simulated_response
from assistant.models import EvaluationAttempt, OpenAIUsageDay, PublicationEvaluation
from assistant.providers import MockProvider, OpenAIProvider, ProviderResponse, image_content, request_content
from assistant.rules import ADMISSION_RULES, SYSTEM_INSTRUCTIONS
from assistant.safety import LiveCallsDisabled, require_live_access
from assistant.services import (
    BudgetExceeded, claim_evaluation, current_evaluation, enqueue_evaluation,
    expire_interrupted_jobs, process_evaluation, reserve_attempt, schedule_evaluation,
    validate_result,
)
from assistant.snapshots import publication_snapshot
from audit.models import AuditEvent
from catalog.models import Category
from core.roles import ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER
from locations.models import RecyclingCenter
from moderation.models import ModerationDecision
from moderation.services import moderate_publication
from publications.models import Publication, PublicationPhoto


def image_upload(name='object.jpg'):
    buffer = io.BytesIO()
    Image.new('RGB', (900, 600), 'green').save(buffer, 'JPEG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


@override_settings(OPENAI_EVALUATION_BACKEND='mock', OPENAI_MODEL='fixture-model')
class EvaluationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.center = RecyclingCenter.objects.create(name='Centro Norte', slug='ai-norte')
        cls.other_center = RecyclingCenter.objects.create(name='Centro Sur', slug='ai-sur')
        cls.citizen = User.objects.create_user(username='ai-citizen', email='ai-citizen@example.com',
                                              habitual_recycling_center=cls.center)
        cls.admin = User.objects.create_user(username='ai-admin', email='ai-admin@example.com')
        cls.admin.groups.add(Group.objects.get_or_create(name=ROLE_STAFF_ADMIN)[0])
        cls.manager = User.objects.create_user(username='ai-manager', email='ai-manager@example.com')
        cls.manager.groups.add(Group.objects.get_or_create(name=ROLE_STAFF_MANAGER)[0])
        UserCenterAccess.objects.create(user=cls.manager, center=cls.center,
                                       role=UserCenterAccess.ScopeRole.SUPERVISOR)
        cls.parent = Category.objects.create(name='Deporte', slug='ai-deporte')
        cls.category = Category.objects.create(name='Pelotas', slug='ai-pelotas', parent=cls.parent)

    def setUp(self):
        self.media = tempfile.TemporaryDirectory(prefix='trueca-ai-test-')
        self.addCleanup(self.media.cleanup)
        self.storage_settings = override_settings(PRIVATE_MEDIA_ROOT=self.media.name)
        self.storage_settings.enable()
        self.addCleanup(self.storage_settings.disable)
        self.publication = Publication.objects.create(
            submitter=self.citizen, title='Pelota', description='Pelota de 20 × 20 × 20 cm.',
            category=self.category, status=Publication.Status.PENDING_REVIEW,
            submitted_at=timezone.now(),
        )
        self.photo = PublicationPhoto.objects.create(publication=self.publication,
                                                    image=image_upload(), is_primary=True)

    def enqueue(self):
        return enqueue_evaluation(self.publication.pk)[0]

    def provider(self, scenario='suitable', error=None):
        provider = Mock(is_simulated=True)
        if error:
            provider.evaluate.side_effect = error
        else:
            provider.evaluate.return_value = ProviderResponse(simulated_response(scenario))
        return provider

    def test_end_to_end_mock_never_changes_administrative_state(self):
        before = (self.publication.status, self.publication.reviewed_at, self.publication.approved_at)
        evaluation = self.enqueue()
        call_command('evaluation_worker', once=True, stdout=io.StringIO())
        evaluation.refresh_from_db()
        self.publication.refresh_from_db()
        self.assertEqual(evaluation.status, 'completed')
        self.assertTrue(evaluation.is_simulated)
        self.assertEqual(evaluation.input_tokens + evaluation.output_tokens, 0)
        self.assertEqual((self.publication.status, self.publication.reviewed_at,
                          self.publication.approved_at), before)
        self.assertFalse(self.publication.moderation_decisions.exists())
        self.assertEqual(OpenAIUsageDay.objects.count(), 0)

    def test_all_fixture_scenarios_validate_and_preserve_manual_decision(self):
        evaluation = self.enqueue()
        for scenario in ('suitable', 'unverifiable', 'visual', 'prohibited', 'small_appliance',
                         'wrong_category', 'incoherent'):
            with self.subTest(scenario=scenario):
                result = validate_result(simulated_response(scenario), evaluation)
                self.assertEqual(len(result['checks']), 10)
                self.assertIn(result['overall'], ('apparently_suitable', 'issues', 'needs_verification'))
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, Publication.Status.PENDING_REVIEW)

    def test_small_appliances_distinguish_delivery_from_catalog(self):
        result = validate_result(simulated_response('small_appliance'), self.enqueue())
        self.assertEqual(result['checks']['admission']['status'], 'INCIDENCIA')
        self.assertIn('obtener puntos', result['checks']['admission']['explanation'])
        self.assertIn('NO pueden publicarse', ADMISSION_RULES['small_appliances'])

    def test_full_flow_with_two_photos_sends_both_once(self):
        PublicationPhoto.objects.create(publication=self.publication, image=image_upload('second.jpg'))
        evaluation = self.enqueue()
        provider = self.provider('incoherent')
        process_evaluation(evaluation.pk, provider=provider)
        process_evaluation(evaluation.pk, provider=provider)
        self.assertEqual(provider.evaluate.call_count, 1)
        self.assertEqual(len(provider.evaluate.call_args.args[1]), 2)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.result['checks']['photo_consistency']['status'], 'INCIDENCIA')

    def test_schema_rejects_invalid_missing_extra_and_wrong_types(self):
        evaluation = self.enqueue()
        valid = simulated_response('suitable')
        invalid_cases = [{}, 'not-json', {**valid, 'approve': True},
                         {**valid, 'overall': 'approved'},
                         {**valid, 'alternative_category_ids': ['123']}]
        for payload in invalid_cases:
            with self.subTest(payload=str(payload)[:60]), self.assertRaises(ValueError):
                validate_result(payload, evaluation)

    def test_nonexistent_or_inactive_category_cannot_be_suggested(self):
        evaluation = self.enqueue()
        payload = simulated_response('wrong_category')
        payload['alternative_category_ids'] = [999999]
        with self.assertRaises(ValueError):
            validate_result(payload, evaluation)
        payload['alternative_category_ids'] = [self.category.pk]
        self.assertEqual(validate_result(payload, evaluation)['alternative_category_ids'], [self.category.pk])
        Category.objects.filter(pk=self.parent.pk).update(is_active=False)
        with self.assertRaises(ValueError):
            validate_result(payload, evaluation)

    def test_visual_intervals_near_limit_require_physical_verification(self):
        result = validate_result(simulated_response('visual'), self.enqueue())
        self.assertTrue(result['dimensions']['physical_verification'])
        self.assertEqual(result['overall'], 'needs_verification')
        self.assertEqual(result['dimensions']['axis_comparison'][0]['status'], 'NO_VERIFICABLE')
        payload = simulated_response('visual')
        payload['dimensions']['height'] = {'minimum_cm': 55.0, 'maximum_cm': 60.0}
        payload['dimensions']['physical_verification'] = False
        result = validate_result(payload, self.enqueue())
        self.assertEqual(result['overall'], 'issues')
        self.assertTrue(result['dimensions']['physical_verification'])

    def test_each_dimension_is_compared_to_limit(self):
        evaluation = self.enqueue()
        for axis in ('height', 'width', 'depth'):
            payload = simulated_response('suitable')
            payload['dimensions'][axis] = {'minimum_cm': 55.0, 'maximum_cm': 55.0}
            result = validate_result(payload, evaluation)
            self.assertEqual(result['overall'], 'issues')
            self.assertEqual(result['checks']['dimensions']['status'], 'INCIDENCIA')

    def test_unverifiable_dimensions_are_not_a_violation(self):
        result = validate_result(simulated_response(), self.enqueue())
        self.assertEqual(result['checks']['dimensions']['status'], 'NO_VERIFICABLE')
        self.assertNotEqual(result['overall'], 'issues')

    def test_dimension_limit_is_taken_from_versioned_rules(self):
        evaluation = self.enqueue()
        payload = simulated_response('suitable')
        payload['dimensions']['height'] = {'minimum_cm': 55.0, 'maximum_cm': 55.0}
        with patch.dict(ADMISSION_RULES, {'max_axis_cm': 60}):
            result = validate_result(payload, evaluation)
        self.assertNotEqual(result['overall'], 'issues')
        self.assertEqual(result['dimensions']['limit_cm'], 60)

    def test_precise_visual_or_inverted_ranges_and_fabricated_sources_are_rejected(self):
        evaluation = self.enqueue()
        for mode in ('visual', 'inverted', 'documented'):
            payload = simulated_response('suitable')
            if mode == 'inverted':
                payload['dimensions']['height'] = {'minimum_cm': 30.0, 'maximum_cm': 10.0}
            else:
                payload['dimensions']['method'] = mode
                payload['dimensions']['source'] = 'https://fabricated.example/model'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_result(payload, evaluation)

    def test_queue_creation_waits_for_commit(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            schedule_evaluation(self.publication.pk)
            self.assertEqual(PublicationEvaluation.objects.count(), 0)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(PublicationEvaluation.objects.count(), 1)

    def test_rollback_does_not_schedule_evaluation(self):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    schedule_evaluation(self.publication.pk)
                    raise ValueError('rollback')
            except ValueError:
                pass
        self.assertFalse(PublicationEvaluation.objects.exists())

    def test_duplicate_enqueue_claim_and_refresh_are_idempotent(self):
        evaluation = self.enqueue()
        self.assertEqual(self.enqueue().pk, evaluation.pk)
        self.assertIsNotNone(claim_evaluation(evaluation.pk))
        self.assertIsNone(claim_evaluation(evaluation.pk))
        self.assertEqual(self.enqueue().pk, evaluation.pk)
        self.assertEqual(PublicationEvaluation.objects.count(), 1)

    def test_database_enforces_one_active_job(self):
        evaluation = self.enqueue()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PublicationEvaluation.objects.create(publication=self.publication,
                                                fingerprint=evaluation.fingerprint, model='test',
                                                rules_version='test', prompt_version='test')

    def test_changed_content_invalidates_old_evaluation_before_api_call(self):
        evaluation = self.enqueue()
        Publication.objects.filter(pk=self.publication.pk).update(title='Título corregido')
        provider = self.provider()
        process_evaluation(evaluation.pk, provider=provider)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'obsolete')
        provider.evaluate.assert_not_called()

    def test_result_arriving_after_edit_cannot_overwrite_current_version(self):
        evaluation = self.enqueue()
        provider = self.provider()

        def delayed(*args, **kwargs):
            Publication.objects.filter(pk=self.publication.pk).update(description='Objeto cambiado')
            return ProviderResponse(simulated_response('suitable'))

        provider.evaluate.side_effect = delayed
        process_evaluation(evaluation.pk, provider=provider)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'obsolete')
        self.assertEqual(evaluation.result, {})
        self.publication.refresh_from_db()
        self.assertIsNone(current_evaluation(self.publication))

    def test_resubmission_even_identical_content_has_new_version_and_history(self):
        old = self.enqueue()
        process_evaluation(old.pk, provider=self.provider())
        self.publication.submitted_at = timezone.now() + timedelta(seconds=1)
        self.publication.save(update_fields=['submitted_at'])
        new = self.enqueue()
        self.assertNotEqual(old.pk, new.pk)
        self.assertEqual(self.publication.ai_evaluations.count(), 2)

    def test_photo_changes_invalidate_version(self):
        old = self.enqueue()
        self.photo.delete()
        self.assertIsNone(current_evaluation(self.publication))
        self.assertNotEqual(self.enqueue().fingerprint, old.fingerprint)

    def test_timeouts_and_network_errors_do_not_retry_ambiguous_calls(self):
        for error, code in [(APITimeoutError(request=httpx.Request('POST', 'https://example.test')), 'timeout_unknown_usage'),
                            (APIConnectionError(request=httpx.Request('POST', 'https://example.test')), 'connection_unknown_usage')]:
            evaluation = self.enqueue()
            provider = self.provider(error=error)
            process_evaluation(evaluation.pk, provider=provider)
            evaluation.refresh_from_db()
            self.assertEqual(evaluation.status, 'error')
            self.assertEqual(evaluation.error_code, code)
            self.assertFalse(process_evaluation(evaluation.pk, provider=provider))
            self.assertEqual(provider.evaluate.call_count, 1)
            PublicationEvaluation.objects.filter(pk=evaluation.pk).delete()

    @override_settings(OPENAI_MAX_ATTEMPTS=2)
    def test_rate_limit_retries_are_delayed_and_bounded(self):
        error = RateLimitError('rate limited', response=httpx.Response(
            429, request=httpx.Request('POST', 'https://example.test')),
            body={'code': 'rate_limit_exceeded'})
        evaluation = self.enqueue()
        provider = self.provider(error=error)
        process_evaluation(evaluation.pk, provider=provider)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'pending')
        self.assertGreater(evaluation.available_at, timezone.now())
        self.assertFalse(process_evaluation(evaluation.pk, provider=provider))
        PublicationEvaluation.objects.filter(pk=evaluation.pk).update(available_at=timezone.now())
        process_evaluation(evaluation.pk, provider=provider)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'error')
        self.assertEqual(provider.evaluate.call_count, 2)

    def test_quota_exhaustion_never_retries(self):
        error = RateLimitError('quota', response=httpx.Response(
            429, request=httpx.Request('POST', 'https://example.test')),
            body={'code': 'insufficient_quota'})
        evaluation = self.enqueue()
        process_evaluation(evaluation.pk, provider=self.provider(error=error))
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'error')
        self.assertEqual(evaluation.error_code, 'quota_exceeded')

    def test_interrupted_worker_is_not_automatically_retried(self):
        evaluation = self.enqueue()
        claim_evaluation(evaluation.pk)
        PublicationEvaluation.objects.filter(pk=evaluation.pk).update(
            started_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(expire_interrupted_jobs(), 1)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'error')
        self.assertIn('unknown_usage', evaluation.error_code)

    def test_invalid_response_does_not_leak_exception_and_keeps_usage(self):
        evaluation = self.enqueue()
        provider = self.provider()
        provider.evaluate.return_value = ProviderResponse('invalid secret data', 100, 20, 'resp_mock')
        process_evaluation(evaluation.pk, provider=provider)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.error_code, 'invalid_response')
        self.assertEqual(evaluation.input_tokens, 100)
        self.assertEqual(evaluation.result, {})
        self.assertEqual(evaluation.usage_attempts.get().response_id, 'resp_mock')
        self.assertEqual(evaluation.error_details, {
            'stage': 'schema', 'violations': [{'field': '$', 'type': 'json_invalid'}],
        })
        self.assertEqual(evaluation.usage_attempts.get().error_details, evaluation.error_details)
        self.assertNotIn('secret', json.dumps(evaluation.error_details))

    @override_settings(OPENAI_MODEL='fixture-model', OPENAI_INPUT_USD_PER_MILLION=Decimal('1'),
                       OPENAI_OUTPUT_USD_PER_MILLION=Decimal('2'),
                       OPENAI_REQUEST_RESERVE_USD=Decimal('.1'),
                       OPENAI_DAILY_BUDGET_USD=Decimal('.15'), OPENAI_DAILY_REQUEST_LIMIT=2)
    def test_budget_reservations_are_enforced_before_provider(self):
        evaluation = self.enqueue()
        reserve_attempt(evaluation, simulated=False)
        with self.assertRaises(BudgetExceeded):
            reserve_attempt(evaluation, simulated=False)
        self.assertEqual(OpenAIUsageDay.objects.get().requests, 1)
        self.assertEqual(EvaluationAttempt.objects.count(), 1)
        with self.assertRaises(BudgetExceeded):
            reserve_attempt(evaluation, simulated=False, budget=Decimal('.01'))

    def test_manual_flow_uses_one_photo_and_no_retry(self):
        PublicationPhoto.objects.create(publication=self.publication, image=image_upload('two.jpg'))
        evaluation = enqueue_evaluation(self.publication.pk, manual=True)[0]
        provider = self.provider(error=RateLimitError('rate', response=httpx.Response(
            429, request=httpx.Request('POST', 'https://example.test')), body={'code': 'rate_limit_exceeded'}))
        process_evaluation(evaluation.pk, provider=provider, manual=True, max_side=512, max_output_tokens=1200)
        self.assertEqual(len(provider.evaluate.call_args.args[1]), 1)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'error')
        self.assertEqual(evaluation.attempts, 1)

    def test_decision_links_to_current_completed_evaluation_only(self):
        evaluation = self.enqueue()
        process_evaluation(evaluation.pk, provider=self.provider('prohibited'))
        _, decision = moderate_publication(publication_id=self.publication.pk, reviewer=self.admin,
                                           decision=ModerationDecision.Decision.APPROVE)
        self.assertEqual(decision.ai_evaluation_id, evaluation.pk)
        self.assertEqual(decision.publication_fingerprint, evaluation.fingerprint)
        self.assertEqual(decision.resulting_status, Publication.Status.APPROVED)

    def test_error_does_not_prevent_manual_review(self):
        evaluation = self.enqueue()
        process_evaluation(evaluation.pk, provider=self.provider(error=ValueError('broken')))
        _, decision = moderate_publication(publication_id=self.publication.pk, reviewer=self.admin,
                                           decision=ModerationDecision.Decision.REJECT, notes='Objeto prohibido')
        self.assertIsNone(decision.ai_evaluation)
        self.assertEqual(decision.resulting_status, Publication.Status.REJECTED)

    def test_stale_evaluation_is_not_attached_to_decision(self):
        evaluation = self.enqueue()
        process_evaluation(evaluation.pk, provider=self.provider())
        Publication.objects.filter(pk=self.publication.pk).update(title='Otra cosa')
        _, decision = moderate_publication(publication_id=self.publication.pk, reviewer=self.admin,
                                           decision=ModerationDecision.Decision.APPROVE)
        self.assertIsNone(decision.ai_evaluation)

    def test_repeat_requires_post_role_scope_and_current_status(self):
        url = reverse('moderation:reevaluate', args=[self.publication.pk])
        self.client.force_login(self.citizen)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.publication.ai_evaluations.count(), 1)
        self.citizen.habitual_recycling_center = self.other_center
        self.citizen.save(update_fields=['habitual_recycling_center'])
        self.assertEqual(self.client.post(url).status_code, 404)
        with self.assertRaises(PermissionDenied):
            enqueue_evaluation(self.publication.pk, requested_by=self.manager, force=True)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url).status_code, 302)

    def test_repeat_is_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(reverse('moderation:reevaluate', args=[self.publication.pk])).status_code, 403)

    def test_repeat_double_click_and_finished_cooldown(self):
        evaluation = self.enqueue()
        same, created = enqueue_evaluation(self.publication.pk, requested_by=self.admin, force=True)
        self.assertFalse(created)
        self.assertEqual(same.pk, evaluation.pk)
        process_evaluation(evaluation.pk, provider=self.provider())
        with self.assertRaises(ValidationError):
            enqueue_evaluation(self.publication.pk, requested_by=self.admin, force=True)
        PublicationEvaluation.objects.filter(pk=evaluation.pk).update(created_at=timezone.now() - timedelta(minutes=2))
        new, created = enqueue_evaluation(self.publication.pk, requested_by=self.admin, force=True)
        self.assertTrue(created)
        self.assertNotEqual(new.pk, evaluation.pk)

    def test_panel_shows_result_simulation_history_and_escapes_output(self):
        evaluation = self.enqueue()
        provider = self.provider()
        provider.evaluate.return_value.payload['identified_object'] = '<script>alert(1)</script>'
        process_evaluation(evaluation.pk, provider=provider)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('moderation:detail', args=[self.publication.pk]))
        self.assertContains(response, 'Evaluación automática')
        self.assertContains(response, 'Simulación de desarrollo')
        self.assertContains(response, 'Historial de evaluaciones')
        self.assertContains(response, 'Repetir análisis')
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_citizen_submit_schedules_after_commit_and_draft_does_not(self):
        self.client.force_login(self.citizen)
        data = {'title': 'Pelota nueva', 'description': 'Pelota deportiva en buen estado.',
                'category': self.category.pk, 'parent_category': self.parent.pk, 'action': 'submit',
                'photos': image_upload('new.jpg'), 'primary_photo': 'new:0'}
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('publications:create'), data)
        self.assertEqual(response.status_code, 302)
        new = Publication.objects.get(title='Pelota nueva')
        self.assertEqual(new.ai_evaluations.count(), 1)
        self.assertEqual(new.ai_evaluations.get().status, 'pending')
        data.update(title='Borrador nuevo', action='save_draft')
        data.pop('photos')
        data['primary_photo'] = ''
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('publications:create'), data)
        self.assertFalse(Publication.objects.get(title='Borrador nuevo').ai_evaluations.exists())

    def test_snapshot_and_request_omit_account_and_contact_fields(self):
        self.publication.description = 'Pelota de 20 cm; correo maria@example.com; teléfono +34 612 345 678.'
        self.publication.save()
        evaluation = self.enqueue()
        content = request_content(evaluation, [self.photo])
        text = content[0]['text']
        for sensitive in (self.citizen.email, 'maria@example.com', '612 345 678', 'habitual_recycling_center'):
            self.assertNotIn(sensitive, text)
        self.assertIn('DATOS NO CONFIABLES', SYSTEM_INSTRUCTIONS)
        self.assertIn('20 cm', text)

    def test_photo_preparation_resizes_and_removes_metadata(self):
        content = image_content(self.photo, max_side=256)
        raw = base64.b64decode(content['image_url'].split(',', 1)[1])
        with Image.open(io.BytesIO(raw)) as image:
            self.assertLessEqual(max(image.size), 256)
            self.assertFalse(image.getexif())
        self.assertEqual(content['detail'], 'high')

    @override_settings(OPENAI_LIVE_ENABLED=True, OPENAI_API_KEY='fake-key')
    def test_live_access_is_blocked_in_tests_even_with_key_and_flags(self):
        with self.assertRaises(LiveCallsDisabled):
            require_live_access()
        with self.assertRaises(CommandError):
            call_command('test_openai_integration', self.publication.pk,
                         authorize_cost=True, budget_usd='.1', stdout=io.StringIO())

    def test_direct_sync_and_async_sdk_calls_are_blocked_before_transport(self):
        with patch('httpx.Client.send') as send, OpenAI(api_key='fake') as client:
            with self.assertRaises(LiveCallsDisabled):
                client.responses.create(model='never-called', input='test')
            send.assert_not_called()

        async def check_async():
            async with AsyncOpenAI(api_key='fake') as client:
                with self.assertRaises(LiveCallsDisabled):
                    await client.responses.create(model='never-called', input='test')

        with patch('httpx.AsyncClient.send') as send:
            asyncio.run(check_async())
            send.assert_not_called()

    def test_manual_command_requires_authorization_without_calling_provider(self):
        with patch('assistant.providers.OpenAIProvider.evaluate') as evaluate:
            with self.assertRaises(CommandError):
                call_command('test_openai_integration', self.publication.pk, budget_usd='.1', stdout=io.StringIO())
            evaluate.assert_not_called()

    def test_worker_cannot_consume_manual_integration_job(self):
        evaluation = enqueue_evaluation(self.publication.pk, manual=True)[0]
        with patch('assistant.management.commands.evaluation_worker.process_evaluation') as process:
            call_command('evaluation_worker', once=True, stdout=io.StringIO())
            process.assert_not_called()
        self.assertIsNone(claim_evaluation(evaluation.pk))
        self.assertIsNotNone(claim_evaluation(evaluation.pk, manual=True))

    @override_settings(OPENAI_INPUT_USD_PER_MILLION=Decimal('1'),
                       OPENAI_OUTPUT_USD_PER_MILLION=Decimal('2'),
                       OPENAI_REQUEST_RESERVE_USD=Decimal('.1'))
    def test_known_usage_replaces_reservation_and_records_actual_estimate(self):
        evaluation = self.enqueue()
        provider = self.provider()
        provider.is_simulated = False  # Mocked transport, exercise the real usage ledger only.
        provider.evaluate.return_value = ProviderResponse(simulated_response('suitable'), 1000, 500, 'resp_mock')
        process_evaluation(evaluation.pk, provider=provider)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.estimated_cost_usd, Decimal('.002'))
        self.assertEqual(OpenAIUsageDay.objects.get().reserved_usd, Decimal('.002'))
        attempt = evaluation.usage_attempts.get()
        self.assertTrue(attempt.usage_known)
        self.assertEqual(attempt.input_rate, Decimal('1'))
        self.assertEqual(attempt.output_rate, Decimal('2'))

    @override_settings(OPENAI_INPUT_USD_PER_MILLION=Decimal('1'),
                       OPENAI_OUTPUT_USD_PER_MILLION=Decimal('2'),
                       OPENAI_REQUEST_RESERVE_USD=Decimal('.1'))
    def test_unknown_usage_keeps_conservative_budget_reservation(self):
        evaluation = self.enqueue()
        provider = self.provider(error=APITimeoutError(request=httpx.Request('POST', 'https://example.test')))
        provider.is_simulated = False
        process_evaluation(evaluation.pk, provider=provider)
        self.assertEqual(OpenAIUsageDay.objects.get().reserved_usd, Decimal('.1'))
        self.assertFalse(evaluation.usage_attempts.get().usage_known)

    @override_settings(OPENAI_INPUT_USD_PER_MILLION=Decimal('1'),
                       OPENAI_OUTPUT_USD_PER_MILLION=Decimal('2'), OPENAI_DAILY_REQUEST_LIMIT=0)
    def test_request_limit_prevents_provider_invocation(self):
        evaluation = self.enqueue()
        provider = self.provider()
        provider.is_simulated = False
        process_evaluation(evaluation.pk, provider=provider)
        provider.evaluate.assert_not_called()
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.error_code, 'budget_exceeded')

    @override_settings(OPENAI_EVALUATION_BACKEND='disabled')
    def test_disabled_provider_does_not_create_usage_attempt(self):
        evaluation = self.enqueue()
        process_evaluation(evaluation.pk)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.error_code, 'live_disabled')
        self.assertFalse(evaluation.usage_attempts.exists())

    @override_settings(OPENAI_LIVE_ENABLED=True, OPENAI_API_KEY='fake-key', OPENAI_MAX_OUTPUT_TOKENS=1200)
    def test_sdk_adapter_parameters_with_mocked_client(self):
        evaluation = self.enqueue()
        fake_response = Mock(status='completed', output_text=json.dumps(simulated_response('suitable')),
                             id='resp_fixture', usage=Mock(input_tokens=100, output_tokens=200),
                             output=[], incomplete_details=None)
        with patch('assistant.providers.require_live_access'), patch('openai.OpenAI') as client_type:
            client = client_type.return_value.__enter__.return_value
            client.responses.create.return_value = fake_response
            result = OpenAIProvider().evaluate(evaluation, [self.photo])
            self.assertEqual(result.input_tokens, 100)
            self.assertEqual(result.output_tokens, 200)
            self.assertEqual(client_type.call_args.kwargs['max_retries'], 0)
            arguments = client.responses.create.call_args.kwargs
            self.assertFalse(arguments['store'])
            self.assertEqual(arguments['max_output_tokens'], 1200)
            self.assertTrue(arguments['text']['format']['strict'])
            self.assertEqual(arguments['input'][0]['content'][1]['type'], 'input_image')

    @override_settings(OPENAI_LIVE_ENABLED=True, OPENAI_API_KEY='fake-key')
    def test_manual_luna_uses_standard_pricing_and_no_reasoning(self):
        evaluation = enqueue_evaluation(self.publication.pk, manual=True)[0]
        evaluation.model = 'gpt-6-luna'
        fake_response = Mock(status='completed', output_text=json.dumps(simulated_response()),
                             id='resp_manual_fixture', usage=Mock(input_tokens=100, output_tokens=200),
                             output=[], incomplete_details=None)
        with patch('assistant.providers.require_live_access'), patch('openai.OpenAI') as client_type:
            client = client_type.return_value.__enter__.return_value
            client.responses.create.return_value = fake_response
            OpenAIProvider().evaluate(evaluation, [self.photo], max_side=512, max_output_tokens=1200)
            client.responses.create.assert_called_once()
            arguments = client.responses.create.call_args.kwargs
            self.assertEqual(arguments['model'], 'gpt-6-luna')
            self.assertEqual(arguments['reasoning'], {'effort': 'none'})
            self.assertEqual(arguments['service_tier'], 'default')
            self.assertEqual(arguments['max_output_tokens'], 1200)
            self.assertFalse(arguments['store'])
            self.assertEqual(client_type.call_args.kwargs['max_retries'], 0)

    def test_summary_contradictions_are_resolved_without_discarding_checks(self):
        evaluation = self.enqueue()
        for overall in ('apparently_suitable', 'needs_verification', 'issues'):
            for status in ('CORRECTO', 'INCIDENCIA', 'NO_VERIFICABLE'):
                with self.subTest(overall=overall, status=status):
                    payload = simulated_response('suitable')
                    payload['overall'] = overall
                    payload['checks']['title']['status'] = status
                    result = validate_result(payload, evaluation)
                    expected = ('issues' if status == 'INCIDENCIA' or overall == 'issues' else
                                'needs_verification' if status == 'NO_VERIFICABLE'
                                or overall == 'needs_verification' else 'apparently_suitable')
                    self.assertEqual(result['overall'], expected)
                    self.assertEqual(result['checks']['title'], payload['checks']['title'])

    def test_uncertain_dimensions_do_not_erase_reported_incidents(self):
        payload = simulated_response('visual')
        payload['checks']['dimensions']['status'] = 'INCIDENCIA'
        payload['checks']['dimensions']['explanation'] = 'La base parece superar el límite.'
        result = validate_result(payload, self.enqueue())
        self.assertEqual(result['checks']['dimensions'], payload['checks']['dimensions'])
        self.assertEqual(result['overall'], 'issues')
        self.assertTrue(result['dimensions']['physical_verification'])

    def test_summary_retains_incidents_and_unverifiable_items_from_lists(self):
        evaluation = self.enqueue()
        for field, expected in (('issues', 'issues'), ('unverifiable', 'needs_verification')):
            with self.subTest(field=field):
                payload = simulated_response('suitable')
                payload[field] = ['Comprobación pendiente.']
                self.assertEqual(validate_result(payload, evaluation)['overall'], expected)

    def test_conflicting_summary_completes_full_mock_flow_once(self):
        evaluation = self.enqueue()
        provider = self.provider('conflicting_summary')
        process_evaluation(evaluation.pk, provider=provider)
        self.assertFalse(process_evaluation(evaluation.pk, provider=provider))
        provider.evaluate.assert_called_once()
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'completed')
        self.assertEqual(evaluation.result['overall'], 'issues')
        self.assertEqual(evaluation.error_details, {})
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, 'pending_review')

    def test_response_edge_cases_keep_usage_without_retry_or_partial_result(self):
        cases = [
            ('incomplete', 'max_output_tokens', False, '{', 'response_incomplete'),
            ('incomplete', 'content_filter', False, '{}', 'response_incomplete'),
            ('incomplete', 'private untrusted text', False, '{}', 'response_incomplete'),
            ('completed', '', True, '', 'response_refused'),
            ('completed', '', True, simulated_response('suitable'), 'response_refused'),
            ('completed', '', False, '  ', 'response_empty'),
            ('failed', '', False, '{}', 'response_failed'),
            ('completed', '', False, '{"checks":', 'invalid_response'),
        ]
        for status, reason, refused, payload, expected in cases:
            with self.subTest(status=status, reason=reason, refused=refused):
                # Separate publication snapshots bypass the intentional repeat cooldown.
                self.publication.description += ' prueba'
                self.publication.save(update_fields=['description'])
                evaluation = self.enqueue()
                provider = self.provider()
                provider.evaluate.return_value = ProviderResponse(
                    payload, 1645, 696, 'resp_mock_edge', status=status,
                    incomplete_reason=reason, refused=refused,
                )
                process_evaluation(evaluation.pk, provider=provider)
                self.assertFalse(process_evaluation(evaluation.pk, provider=provider))
                provider.evaluate.assert_called_once()
                evaluation.refresh_from_db()
                self.assertEqual(evaluation.status, 'error')
                self.assertEqual(evaluation.error_code, expected)
                self.assertEqual(evaluation.result, {})
                self.assertEqual((evaluation.input_tokens, evaluation.output_tokens), (1645, 696))
                attempt = evaluation.usage_attempts.get()
                self.assertEqual(attempt.error_details, evaluation.error_details)
                event = AuditEvent.objects.get(action='publication.ai.error',
                                               metadata__evaluation_id=evaluation.pk)
                self.assertEqual(event.metadata['error_details'], evaluation.error_details)
                self.assertNotIn('private', json.dumps(evaluation.error_details))

    @override_settings(OPENAI_API_KEY='fake-key')
    def test_sdk_adapter_preserves_status_reason_and_refusal_using_mocked_client(self):
        evaluation = self.enqueue()
        for status, reason, refused in (('incomplete', 'max_output_tokens', False),
                                        ('incomplete', 'content_filter', False),
                                        ('completed', None, True)):
            with self.subTest(status=status, reason=reason):
                output = [Mock(type='reasoning'), Mock(type='message', content=[
                    Mock(type='refusal' if refused else 'output_text'),
                ])]
                fake_response = Mock(
                    status=status, incomplete_details=Mock(reason=reason) if reason else None,
                    output=output, output_text='', id='resp_mock_adapter',
                    usage=Mock(input_tokens=1645, output_tokens=696),
                )
                with patch('assistant.providers.require_live_access'), patch('openai.OpenAI') as factory:
                    client = factory.return_value.__enter__.return_value
                    client.responses.create.return_value = fake_response
                    result = OpenAIProvider().evaluate(evaluation, [self.photo])
                    client.responses.create.assert_called_once()
                    self.assertEqual(factory.call_args.kwargs['max_retries'], 0)
                self.assertEqual(result.status, status)
                self.assertEqual(result.incomplete_reason, reason or '')
                self.assertEqual(result.refused, refused)
                self.assertEqual((result.input_tokens, result.output_tokens), (1645, 696))

    def test_validation_diagnostics_hide_values_unknown_field_names_and_context(self):
        evaluation = self.enqueue()
        provider = self.provider()
        payload = simulated_response('suitable')
        payload['sensitive@email.test'] = 'private value'
        payload['checks']['title']['status'] = 'private status'
        payload['dimensions']['height'] = {'minimum_cm': 30, 'maximum_cm': 10}
        provider.evaluate.return_value = ProviderResponse(payload)
        process_evaluation(evaluation.pk, provider=provider)
        evaluation.refresh_from_db()
        serialized = json.dumps(evaluation.error_details)
        self.assertNotIn('private', serialized)
        self.assertNotIn('sensitive', serialized)
        self.assertIn('checks.title.status', serialized)
        self.assertIn('dimensions.height', serialized)
        self.assertIn('?', serialized)
        self.assertLessEqual(len(evaluation.error_details['violations']), 10)

    def test_business_validation_records_safe_rule_diagnostics(self):
        evaluation = self.enqueue()
        provider = self.provider('wrong_category')
        provider.evaluate.return_value.payload['alternative_category_ids'] = [999999]
        process_evaluation(evaluation.pk, provider=provider)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.error_details, {
            'stage': 'business', 'rule': 'category_not_allowed',
        })
        self.assertEqual(evaluation.error_code, 'invalid_response')

    def test_panel_explains_incomplete_response_and_legacy_invalid_response(self):
        evaluation = self.enqueue()
        provider = self.provider()
        provider.evaluate.return_value = ProviderResponse(
            '', status='incomplete', incomplete_reason='max_output_tokens',
        )
        process_evaluation(evaluation.pk, provider=provider)
        self.client.force_login(self.admin)
        url = reverse('moderation:detail', args=[self.publication.pk])
        self.assertContains(self.client.get(url), 'Se alcanzó el límite de tokens de salida.')
        PublicationEvaluation.objects.filter(pk=evaluation.pk).update(
            error_code='invalid_response', error_details={},
        )
        self.assertContains(self.client.get(url), 'puede no haber un diagnóstico guardado')

    def test_request_distinguishes_root_category_and_selected_subcategory(self):
        evaluation = self.enqueue()
        data = json.loads(request_content(evaluation, [self.photo])[0]['text'])
        selection = data['classification']
        self.assertEqual(data['category_id'], self.category.pk)  # Existing FK is the leaf.
        self.assertEqual(selection['category'], {
            'id': self.parent.pk, 'name': 'Deporte', 'parent_id': None,
        })
        self.assertEqual(selection['subcategory'], {
            'id': self.category.pk, 'name': 'Pelotas', 'parent_id': self.parent.pk,
        })
        self.assertEqual([node['name'] for node in selection['path']], ['Deporte', 'Pelotas'])
        self.assertTrue(selection['requires_subcategory'])
        self.assertTrue(selection['is_available'])

    def test_nested_subcategory_sends_full_path_and_root(self):
        leaf = Category.objects.create(name='Tenis', slug='ai-tenis', parent=self.category)
        self.publication.category = leaf
        self.publication.save(update_fields=['category'])
        evaluation = self.enqueue()
        data = json.loads(request_content(evaluation, [self.photo])[0]['text'])
        selection = data['classification']
        self.assertEqual(selection['category']['id'], self.parent.pk)
        self.assertEqual(selection['subcategory']['id'], leaf.pk)
        self.assertEqual([node['name'] for node in selection['path']], ['Deporte', 'Pelotas', 'Tenis'])
        process_evaluation(evaluation.pk, provider=self.provider())
        self.client.force_login(self.admin)
        response = self.client.get(reverse('moderation:detail', args=[self.publication.pk]))
        self.assertContains(response, 'Selección evaluada: Deporte')
        self.assertContains(response, 'Selección evaluada: Deporte / Pelotas / Tenis')

    def test_correct_root_with_wrong_subcategory_is_an_independent_incident(self):
        wrong = Category.objects.create(name='Raquetas', slug='ai-raquetas', parent=self.parent)
        self.publication.category = wrong
        self.publication.save(update_fields=['category'])
        evaluation = self.enqueue()
        provider = self.provider('wrong_subcategory')
        provider.evaluate.return_value.payload['alternative_category_ids'] = [self.category.pk]
        process_evaluation(evaluation.pk, provider=provider)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'completed')
        self.assertEqual(evaluation.result['checks']['category']['status'], 'CORRECTO')
        self.assertEqual(evaluation.result['checks']['subcategory']['status'], 'INCIDENCIA')
        self.assertIn('Raquetas', evaluation.result['checks']['subcategory']['explanation'])
        self.assertEqual(evaluation.result['alternative_category_ids'], [self.category.pk])
        self.assertEqual(evaluation.result['overall'], 'issues')
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.category_id, wrong.pk)
        self.assertEqual(self.publication.status, 'pending_review')
        self.assertFalse(self.publication.moderation_decisions.exists())
        provider.evaluate.assert_called_once()
        self.client.force_login(self.admin)
        response = self.client.get(reverse('moderation:detail', args=[self.publication.pk]))
        self.assertContains(response, 'Selección evaluada: Deporte')
        self.assertContains(response, 'Selección evaluada: Deporte / Raquetas')
        self.assertContains(response, 'Subcategoría')

    def test_required_missing_subcategory_cannot_be_reported_correct(self):
        self.publication.category = self.parent
        self.publication.save(update_fields=['category'])
        evaluation = self.enqueue()
        self.assertIsNone(evaluation.snapshot['classification']['subcategory'])
        self.assertTrue(evaluation.snapshot['classification']['requires_subcategory'])
        result = validate_result(simulated_response('suitable'), evaluation)
        self.assertEqual(result['checks']['category']['status'], 'CORRECTO')
        self.assertEqual(result['checks']['subcategory']['status'], 'INCIDENCIA')
        self.assertIn('Falta seleccionar', result['checks']['subcategory']['explanation'])
        self.assertEqual(result['overall'], 'issues')

    def test_root_without_subcategories_explicitly_reports_not_applicable(self):
        root = Category.objects.create(name='Sin niveles', slug='ai-sin-niveles')
        self.publication.category = root
        self.publication.save(update_fields=['category'])
        evaluation = self.enqueue()
        selection = evaluation.snapshot['classification']
        self.assertEqual(selection['category']['id'], root.pk)
        self.assertIsNone(selection['subcategory'])
        self.assertFalse(selection['requires_subcategory'])
        result = validate_result(simulated_response('suitable'), evaluation)
        self.assertEqual(result['checks']['subcategory']['status'], 'CORRECTO')
        self.assertIn('No aplica', result['checks']['subcategory']['explanation'])
        process_evaluation(evaluation.pk, provider=self.provider())
        self.client.force_login(self.admin)
        response = self.client.get(reverse('moderation:detail', args=[self.publication.pk]))
        self.assertContains(response, 'Selección evaluada: No aplica')

    def test_subcategory_check_remains_mandatory_in_response(self):
        evaluation = self.enqueue()
        provider = self.provider()
        del provider.evaluate.return_value.payload['checks']['subcategory']
        process_evaluation(evaluation.pk, provider=provider)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'error')
        self.assertEqual(evaluation.error_code, 'invalid_response')
        self.assertIn({'field': 'checks.subcategory', 'type': 'missing'},
                      evaluation.error_details['violations'])

    def test_changing_subcategory_invalidates_evaluation_before_provider_call(self):
        evaluation = self.enqueue()
        sibling = Category.objects.create(name='Raquetas', slug='ai-raquetas', parent=self.parent)
        self.publication.category = sibling
        self.publication.save(update_fields=['category'])
        provider = self.provider()
        process_evaluation(evaluation.pk, provider=provider)
        provider.evaluate.assert_not_called()
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'obsolete')
        self.assertIsNone(current_evaluation(self.publication))

    def test_reparented_subcategory_uses_fresh_catalog_path(self):
        before, _ = publication_snapshot(self.publication)
        root = Category.objects.create(name='Otro ámbito', slug='ai-otro-ambito')
        Category.objects.filter(pk=self.category.pk).update(parent=root)
        after, snapshot = publication_snapshot(self.publication)
        self.assertNotEqual(before, after)
        self.assertEqual(snapshot['classification']['category']['id'], root.pk)
        self.assertEqual([node['name'] for node in snapshot['classification']['path']],
                         ['Otro ámbito', 'Pelotas'])

    def test_inactive_subcategory_cannot_be_reported_correct(self):
        Category.objects.filter(pk=self.category.pk).update(is_active=False)
        evaluation = self.enqueue()
        selection = evaluation.snapshot['classification']
        self.assertFalse(selection['is_available'])
        self.assertEqual(selection['subcategory']['name'], 'Pelotas')
        result = validate_result(simulated_response('suitable'), evaluation)
        self.assertEqual(result['checks']['category']['status'], 'CORRECTO')
        self.assertEqual(result['checks']['subcategory']['status'], 'INCIDENCIA')
        self.assertEqual(result['overall'], 'issues')

    def test_inactive_root_marks_both_selected_levels_unavailable(self):
        Category.objects.filter(pk=self.parent.pk).update(is_active=False)
        evaluation = self.enqueue()
        result = validate_result(simulated_response('suitable'), evaluation)
        self.assertEqual(result['checks']['category']['status'], 'INCIDENCIA')
        self.assertEqual(result['checks']['subcategory']['status'], 'INCIDENCIA')
        self.assertFalse(evaluation.snapshot['classification']['is_available'])

    def test_mock_explains_both_selected_levels_without_identifying_photos(self):
        evaluation = self.enqueue()
        response = MockProvider().evaluate(evaluation, [self.photo])
        self.assertIn('Deporte', response.payload['checks']['category']['explanation'])
        self.assertIn('Pelotas', response.payload['checks']['subcategory']['explanation'])
        self.assertEqual(response.payload['checks']['category']['status'], 'NO_VERIFICABLE')
        self.assertEqual(response.payload['checks']['subcategory']['status'], 'NO_VERIFICABLE')
        self.assertIn('No se han analizado fotografías', response.payload['dimensions']['evidence'])

    def test_http_errors_preserve_safe_diagnostics_without_retry(self):
        for status, expected in ((400, 'invalid_request'), (401, 'authentication_error'),
                                 (403, 'permission_denied'), (404, 'model_not_found'),
                                 (500, 'server_error_unknown_usage'), (502, 'server_error_unknown_usage')):
            with self.subTest(status=status):
                self.publication.description += ' prueba'
                self.publication.save(update_fields=['description'])
                evaluation = self.enqueue()
                error = APIStatusError('private provider message', response=httpx.Response(
                    status, request=httpx.Request('POST', 'https://example.test')),
                    body={'code': 'invalid_json_schema', 'param': 'text.format.schema',
                          'message': 'private response'})
                provider = self.provider(error=error)
                process_evaluation(evaluation.pk, provider=provider)
                self.assertFalse(process_evaluation(evaluation.pk, provider=provider))
                provider.evaluate.assert_called_once()
                evaluation.refresh_from_db()
                self.assertEqual(evaluation.error_code, expected)
                self.assertEqual(evaluation.error_details, {
                    'stage': 'http', 'status': status, 'provider_code': 'invalid_json_schema',
                    'parameter': 'text.format.schema',
                })
                self.assertEqual(evaluation.usage_attempts.get().error_details, evaluation.error_details)
                self.assertNotIn('private', json.dumps(evaluation.error_details))

    def test_unknown_http_error_fields_and_internal_messages_are_hidden(self):
        for code, param in (('private response', 'private input'), ({'private': 'value'}, ['private'])):
            with self.subTest(code_type=type(code).__name__):
                self.publication.description += ' prueba'
                self.publication.save(update_fields=['description'])
                evaluation = self.enqueue()
                provider = self.provider(error=APIStatusError('private message', response=httpx.Response(
                    400, request=httpx.Request('POST', 'https://example.test')),
                    body={'code': code, 'param': param}))
                process_evaluation(evaluation.pk, provider=provider)
                evaluation.refresh_from_db()
                self.assertEqual(evaluation.error_details['provider_code'], 'other')
                self.assertEqual(evaluation.error_details['parameter'], 'other')
                self.assertNotIn('private', json.dumps(evaluation.error_details))
        self.publication.description += ' prueba'
        self.publication.save(update_fields=['description'])
        evaluation = self.enqueue()
        provider = self.provider(error=KeyError('private field value'))
        process_evaluation(evaluation.pk, provider=provider)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.error_details, {'stage': 'internal', 'exception_type': 'KeyError'})

    @override_settings(OPENAI_API_KEY='fake-key')
    def test_sdk_sends_strict_schema_without_ref_siblings_and_keeps_check_descriptions(self):
        evaluation = self.enqueue()
        fake_response = Mock(
            status='completed', output_text=json.dumps(simulated_response('suitable')),
            id='resp_schema_fixture', usage=Mock(input_tokens=100, output_tokens=200),
            output=[], incomplete_details=None,
        )

        def assert_supported(node):
            if isinstance(node, dict):
                if '$ref' in node:
                    self.assertEqual(set(node), {'$ref'})
                if node.get('type') == 'object':
                    self.assertFalse(node['additionalProperties'])
                    self.assertEqual(set(node['required']), set(node['properties']))
                for value in node.values():
                    assert_supported(value)
            elif isinstance(node, list):
                for value in node:
                    assert_supported(value)

        def api_fixture(**kwargs):
            # Check the outgoing contract at the mocked API boundary, not just the model.
            schema = kwargs['text']['format']['schema']
            assert_supported(schema)
            checks = schema['$defs']['Checks']['properties']
            self.assertIn('classification.category', checks['category']['description'])
            self.assertIn('classification.subcategory', checks['subcategory']['description'])
            self.assertIn('subcategory', schema['$defs']['Checks']['required'])
            return fake_response

        with patch('assistant.providers.require_live_access'), patch('openai.OpenAI') as factory:
            client = factory.return_value.__enter__.return_value
            client.responses.create.side_effect = api_fixture
            response = OpenAIProvider().evaluate(evaluation, [self.photo])
            client.responses.create.assert_called_once()
        self.assertEqual(response.input_tokens, 100)
        self.assertEqual(response.output_tokens, 200)
