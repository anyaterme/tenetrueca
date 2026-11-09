from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from chatbot.models import ChatMessage, ChatSessionUsage
from chatbot.tests.fixtures import ProviderEvidenceMixin


@override_settings(CHATBOT_ENABLED=True, CHATBOT_PROVIDER='mock', CHATBOT_RATE_LIMIT_REQUESTS=2,
                   CHATBOT_RATE_LIMIT_WINDOW_SECONDS=60)
class ChatbotRateLimitTests(ProviderEvidenceMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']

    def send(self, key=None, client=None, conversation=None):
        return (client or self.client).post('/chatbot/api/messages/', {
            'conversation_id': conversation or self.conversation, 'request_id': str(key or uuid4()), 'content': 'Hello',
        }, content_type='application/json')

    def test_rolling_rate_limit_blocks_before_calling_provider_or_reserving_quota(self):
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.send().status_code, 200)
        with patch('chatbot.services.get_provider') as provider:
            response = self.send()
            self.assertEqual(response.status_code, 429)
            provider.assert_not_called()
        self.assertEqual(response.json(), {'error': 'rate_limited'})
        self.assertTrue(1 <= int(response['Retry-After']) <= 60)
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (2, 0))
        self.assertEqual(ChatMessage.objects.count(), 4)

    def test_duplicate_is_replayed_even_when_the_rate_limit_is_full(self):
        key = uuid4()
        self.send(key)
        self.send()
        with patch('chatbot.services.get_provider') as provider:
            self.assertEqual(self.send(key).status_code, 200)
            provider.assert_not_called()
        self.assertEqual(ChatMessage.objects.filter(role='user').count(), 2)

    def test_new_conversation_does_not_reset_frequency_limit(self):
        self.send()
        self.send()
        new = self.client.post('/chatbot/api/reset/').json()['conversation']['id']
        self.assertEqual(self.send(conversation=new).status_code, 429)

    def test_failed_provider_calls_count_toward_frequency_but_not_anonymous_quota(self):
        with patch('chatbot.services.get_provider') as provider:
            provider.return_value.respond.side_effect = RuntimeError('private details')
            self.assertEqual(self.send().status_code, 502)
            self.assertEqual(self.send().status_code, 502)
            self.assertEqual(self.send().status_code, 429)
            self.assertEqual(provider.return_value.respond.call_count, 2)
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (0, 0))

    def test_limit_recovers_when_window_expires(self):
        self.send()
        self.send()
        ChatMessage.objects.update(created_at=timezone.now() - timedelta(seconds=61))
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 3)

    def test_anonymous_sessions_are_isolated_and_tabs_share_the_limit(self):
        self.send()
        self.send()
        tab = Client()
        tab.cookies = self.client.cookies.copy()
        self.assertEqual(self.send(client=tab).status_code, 429)
        independent = Client()
        conversation = independent.get('/chatbot/api/session/').json()['conversation']['id']
        self.assertEqual(self.send(client=independent, conversation=conversation).status_code, 200)

    def test_authenticated_users_share_frequency_across_sessions_without_fixed_quota(self):
        user = get_user_model().objects.create_user(email='rate-auth@example.test')
        first, second = Client(), Client()
        first.force_login(user)
        second.force_login(user)
        conversation = first.get('/chatbot/api/session/').json()['conversation']['id']
        self.assertEqual(second.get('/chatbot/api/session/').json()['conversation']['id'], conversation)
        self.assertEqual(self.send(client=first, conversation=conversation).status_code, 200)
        response = self.send(client=second, conversation=conversation)
        self.assertIsNone(response.json()['usage']['limit'])
        self.assertEqual(self.send(client=second, conversation=conversation).status_code, 429)
        ChatMessage.objects.filter(conversation_id=conversation).update(created_at=timezone.now()-timedelta(seconds=61))
        self.assertEqual(self.send(client=first, conversation=conversation).status_code, 200)
