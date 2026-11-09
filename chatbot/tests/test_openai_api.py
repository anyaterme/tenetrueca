from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import translation
from openai import APITimeoutError, AuthenticationError, RateLimitError

from chatbot.models import ChatMessage, ChatSessionUsage
from chatbot.tests.test_openai_provider import response
from chatbot.tests.fixtures import ProviderEvidenceMixin, selection_json


@override_settings(CHATBOT_ENABLED=True, CHATBOT_PROVIDER='openai', OPENAI_API_KEY='offline-test-key',
                   CHATBOT_HISTORY_MESSAGES=3, CHATBOT_RATE_LIMIT_REQUESTS=1000)
class OpenAIAPITests(ProviderEvidenceMixin, TestCase):
    def setUp(self):
        super().setUp()
        guard = patch('chatbot.safety.is_test_process', return_value=False)
        guard.start()
        self.addCleanup(guard.stop)
        factory = patch('chatbot.providers.AsyncOpenAI')
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        self.client_sdk = self.factory.return_value.__aenter__.return_value
        self.create = AsyncMock(return_value=response(selection_json(f'faq:{self.provider_faq.pk}')))
        self.client_sdk.responses.create = self.create
        self.conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']

    def send(self, key=None, content='¿Cuántos puntos recibo?'):
        return self.client.post('/chatbot/api/messages/', {'conversation_id': self.conversation,
            'request_id': str(key or uuid4()), 'content': content}, content_type='application/json')

    def test_spanish_and_english_end_to_end_use_mocked_responses_and_consume_one_each(self):
        with translation.override('es'):
            result = self.send()
        self.assertEqual(result.status_code, 200)
        self.assertIn('español', self.create.call_args.kwargs['instructions'])
        self.create.return_value = response(selection_json(f'faq:{self.provider_faq.pk}', display_text='Answer'))
        with translation.override('en'):
            result = self.send(content='How many points do I receive?')
        self.assertEqual(result.status_code, 200)
        self.assertIn('Responde en inglés', self.create.call_args.kwargs['instructions'])
        self.assertEqual(result.json()['messages'][-1]['content'], 'Answer')
        self.assertEqual(result.json()['usage']['consumed'], 2)

    def test_history_is_loaded_from_database_and_recent_only(self):
        self.send(content='Old question not needed')
        self.send(content='Recent question')
        self.send(content='Current question')
        history = self.create.call_args.kwargs['input']
        self.assertEqual([m['role'] for m in history], ['user', 'user'])
        self.assertIn('Current question', history[0]['content'])
        self.assertIn('EVIDENCIA PÚBLICA', history[1]['content'])
        self.assertNotIn('Old question not needed', str(history))
        self.assertEqual(ChatMessage.objects.count(), 6)

    def test_duplicate_request_uses_stored_answer_and_calls_sdk_once(self):
        key = uuid4()
        first = self.send(key)
        second = self.send(key)
        self.assertEqual(first.json(), second.json())
        self.create.assert_awaited_once()
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 1)
        self.assertEqual(ChatMessage.objects.count(), 2)

    def test_sdk_errors_and_empty_or_partial_answers_release_quota_and_do_not_leak_details(self):
        request = httpx.Request('POST', 'https://api.openai.com/v1/responses')
        errors = [APITimeoutError(request=request),
                  AuthenticationError('private-key-details', response=httpx.Response(401, request=request), body={}),
                  RateLimitError('private-key-details', response=httpx.Response(429, request=request), body={})]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.create.side_effect = error
                result = self.send()
                self.assertEqual(result.status_code, 502)
                self.assertNotIn('private-key-details', result.content.decode())
        self.create.side_effect = None
        for answer in (response(''), response('partial', 'incomplete')):
            self.create.return_value = answer
            self.assertEqual(self.send().status_code, 502)
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (0, 0))

    def test_response_persistence_failure_does_not_consume_quota_or_repeat_sdk_call(self):
        key = uuid4()
        original = ChatMessage.save
        def save(message, *args, **kwargs):
            if message.role == 'user' and message.status == 'completed':
                raise DatabaseError('Simulated persistence error')
            return original(message, *args, **kwargs)
        with patch.object(ChatMessage, 'save', autospec=True, side_effect=save):
            self.assertEqual(self.send(key).status_code, 503)
        self.assertEqual(self.send(key).status_code, 502)
        self.create.assert_awaited_once()
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (0, 0))
        self.assertEqual(ChatMessage.objects.get().status, 'failed')

    @override_settings(OPENAI_API_KEY='')
    def test_missing_configuration_fails_safely_without_consuming_quota(self):
        result = self.send()
        self.assertEqual(result.status_code, 502)
        self.factory.assert_not_called()
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 0)
        self.assertEqual(ChatSessionUsage.objects.get().pending_requests, 0)

    def test_global_test_detector_blocks_client_even_if_live_settings_are_present(self):
        with patch('chatbot.safety.is_test_process', return_value=True):
            self.assertEqual(self.send().status_code, 502)
        self.factory.assert_not_called()

    def test_authenticated_user_uses_provider_without_anonymous_fixed_limit(self):
        user = get_user_model().objects.create_user(email='openai-api@example.test')
        self.client.force_login(user)
        self.conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']
        result = self.send()
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json()['usage']['limit'])
        self.create.assert_awaited_once()


    def test_sensitive_upstream_error_and_question_are_not_logged(self):
        request = httpx.Request('POST', 'https://api.openai.com/v1/responses')
        self.create.side_effect = AuthenticationError('AUDIT_SECRET_MARKER',
            response=httpx.Response(401, request=request), body={'private': 'AUDIT_SECRET_MARKER'})
        with self.assertLogs('chatbot.services', level='WARNING') as captured:
            result = self.send(content='AUDIT_PRIVATE_QUESTION')
        self.assertEqual(result.status_code, 502)
        logs = '\n'.join(captured.output)
        self.assertNotIn('AUDIT_SECRET_MARKER', logs)
        self.assertNotIn('AUDIT_PRIVATE_QUESTION', logs)
        self.assertIn('reason=authentication', logs)
