import json
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from chatbot.checks import chatbot_configuration
from chatbot.models import ChatConversation, ChatMessage, ChatSessionUsage
from chatbot.providers import ChatTurn, SimulatedProvider
from chatbot.safety import ProviderError
from chatbot.tests.fixtures import ProviderEvidenceMixin
from chatbot.services import (Owner, finish_message, get_session, reap_expired_reservations,
                              reserve_message, send_message)


@override_settings(CHATBOT_ENABLED=True, CHATBOT_ANONYMOUS_LIMIT=10,
                   CHATBOT_MAX_INPUT_CHARS=2000, CHATBOT_HISTORY_MESSAGES=20)
class ChatbotAPITests(ProviderEvidenceMixin, TestCase):
    def bootstrap(self, client=None):
        response = (client or self.client).get('/chatbot/api/session/')
        self.assertEqual(response.status_code, 200)
        return response.json()['conversation']['id']

    def post(self, conversation, content='Hola', request_id=None, client=None):
        return (client or self.client).post('/chatbot/api/messages/', data=json.dumps({
            'conversation_id': str(conversation), 'request_id': str(request_id or uuid4()), 'content': content,
        }), content_type='application/json')

    def owner(self):
        return Owner(session_key=self.client.session.session_key)

    def reserve(self, conversation, key=None):
        return reserve_message(self.owner(), conversation, key or uuid4(), 'Pendiente')[0]

    def test_anonymous_session_created_recovered_and_shared_across_tabs(self):
        conversation = self.bootstrap()
        self.assertEqual(self.bootstrap(), conversation)
        other_tab = Client()
        other_tab.cookies = self.client.cookies.copy()
        self.assertEqual(self.bootstrap(other_tab), conversation)
        self.post(conversation)
        recovered = other_tab.get('/chatbot/api/session/').json()
        self.assertEqual(len(recovered['messages']), 2)
        self.assertEqual(recovered['usage'], {'limit': 10, 'consumed': 1, 'pending': 0, 'remaining': 9})
        self.assertEqual(ChatConversation.objects.count(), 1)
        self.assertTrue(Session.objects.filter(session_key=self.owner().session_key).exists())

    def test_authenticated_owner_persists_across_login_sessions_without_fixed_limit(self):
        user = get_user_model().objects.create_user(email='chat@example.test')
        self.client.force_login(user)
        conversation = self.bootstrap()
        for _ in range(11):
            response = self.post(conversation)
            self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['usage']['limit'])
        self.assertIsNone(response.json()['usage']['remaining'])
        other = Client()
        other.force_login(user)
        self.assertEqual(self.bootstrap(other), conversation)
        self.assertEqual(ChatSessionUsage.objects.count(), 0)
        self.assertEqual(ChatConversation.objects.get().user, user)

    def test_ten_successful_questions_and_eleventh_blocked(self):
        conversation = self.bootstrap()
        for i in range(10):
            response = self.post(conversation, f'Pregunta {i}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['usage']['remaining'], 9 - i)
        with patch('chatbot.services.get_provider') as provider:
            response = self.post(conversation)
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.json()['error'], 'quota_exhausted')
            provider.assert_not_called()
        self.assertEqual(ChatMessage.objects.count(), 20)
        self.assertEqual(ChatSessionUsage.objects.get().pending_requests, 0)

    def test_reset_keeps_quota_and_old_history(self):
        conversation = self.bootstrap()
        self.post(conversation)
        response = self.client.post('/chatbot/api/reset/')
        self.assertEqual(response.status_code, 200)
        new = response.json()
        self.assertNotEqual(new['conversation']['id'], conversation)
        self.assertEqual(new['messages'], [])
        self.assertEqual(new['usage']['remaining'], 9)
        self.assertEqual(self.bootstrap(), new['conversation']['id'])
        old = self.client.get('/chatbot/api/session/', {'conversation_id': conversation}).json()
        self.assertEqual(old['conversation']['status'], 'closed')
        self.assertEqual(len(old['messages']), 2)
        self.assertEqual(self.post(conversation).status_code, 409)

    def test_reset_does_not_restore_exhausted_quota(self):
        conversation = self.bootstrap()
        for _ in range(10):
            self.post(conversation)
        new = self.client.post('/chatbot/api/reset/').json()
        self.assertEqual(new['usage']['remaining'], 0)
        self.assertEqual(self.post(new['conversation']['id']).status_code, 429)

    def test_provider_error_releases_quota_and_duplicate_failure_is_terminal(self):
        conversation = self.bootstrap()
        key = uuid4()
        with patch('chatbot.services.get_provider') as factory:
            factory.return_value.respond.side_effect = RuntimeError('private provider details')
            response = self.post(conversation, request_id=key)
            duplicate = self.post(conversation, request_id=key)
            self.assertEqual(response.status_code, 502)
            self.assertEqual(duplicate.status_code, 502)
            factory.return_value.respond.assert_called_once()
        self.assertNotIn('private provider details', response.content.decode())
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (0, 0))
        self.assertEqual(self.post(conversation).json()['usage']['remaining'], 9)

    def test_invalid_generation_uses_safe_counted_fallback(self):
        conversation = self.bootstrap()
        for answer in ('', '  ', None, {}, 15):
            with self.subTest(answer=answer), patch('chatbot.services.get_provider') as factory:
                factory.return_value.respond.return_value = answer
                result = self.post(conversation)
                self.assertEqual(result.status_code, 200)
                self.assertIn('Answer', result.json()['messages'][-1]['content'])
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (5, 0))

    def test_history_loading_error_releases_reservation(self):
        conversation = self.bootstrap()
        with patch('chatbot.services.ConversationAnswerer', side_effect=RuntimeError):
            self.assertEqual(self.post(conversation).status_code, 502)
        self.assertEqual(ChatSessionUsage.objects.get().pending_requests, 0)

    def test_persistence_failure_rolls_back_answer_and_releases_reservation(self):
        conversation = self.bootstrap()
        original_save = ChatMessage.save

        def save(message, *args, **kwargs):
            if message.role == 'user' and message.status == 'completed':
                raise DatabaseError('Simulated write failure')
            return original_save(message, *args, **kwargs)

        with patch.object(ChatMessage, 'save', autospec=True, side_effect=save):
            response = self.post(conversation)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'service_unavailable')
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (0, 0))
        self.assertEqual(ChatMessage.objects.get().status, 'failed')
        self.assertEqual(self.post(conversation).status_code, 200)

    def test_expired_duplicate_stays_failed_without_reinvoking_provider(self):
        conversation = self.bootstrap()
        key = uuid4()
        message = self.reserve(conversation, key)
        ChatMessage.objects.filter(pk=message.pk).update(
            reservation_expires_at=timezone.now() - timedelta(seconds=1))
        with patch('chatbot.services.get_provider') as factory:
            response = self.post(conversation, content='Pendiente', request_id=key)
            self.assertEqual(response.status_code, 502)
            factory.assert_not_called()
        self.assertEqual(response.json()['usage']['remaining'], 10)
        self.assertEqual(response.json()['usage']['pending'], 0)

    @override_settings(CHATBOT_GENERATION_MODE='full_faq')
    def test_full_faq_timeout_and_duplicate_leave_quota_uncharged(self):
        conversation = self.bootstrap()
        key = uuid4()
        provider = Mock()
        provider.respond.side_effect = ProviderError('timeout')
        with patch('chatbot.services.get_provider', return_value=provider):
            first = self.post(conversation, '¿Cuántos puntos recibo?', request_id=key)
            duplicate = self.post(conversation, '¿Cuántos puntos recibo?', request_id=key)
        self.assertEqual((first.status_code, duplicate.status_code), (502, 502))
        self.assertEqual(first.json(), duplicate.json())
        self.assertEqual(first.json()['error'], 'response_failed')
        self.assertEqual(first.json()['usage']['consumed'], 0)
        self.assertEqual(first.json()['usage']['pending'], 0)
        self.assertFalse(ChatMessage.objects.filter(role='assistant').exists())
        provider.respond.assert_called_once()

    def test_private_history_responses_cannot_be_cached(self):
        conversation = self.bootstrap()
        for response in (self.client.get('/chatbot/api/session/'), self.post(conversation),
                         self.client.post('/chatbot/api/reset/')):
            self.assertIn('no-store', response['Cache-Control'])
            self.assertIn('private', response['Cache-Control'])

    def test_successful_duplicate_uses_persisted_answer_even_after_quota_exhausted(self):
        conversation = self.bootstrap()
        key = uuid4()
        with patch('chatbot.services.get_provider', return_value=SimulatedProvider()) as factory:
            first = self.post(conversation, request_id=key)
            second = self.post(conversation, request_id=key)
            factory.assert_called_once()
        self.assertEqual(first.json(), second.json())
        for _ in range(9):
            self.post(conversation)
        self.assertEqual(self.post(conversation, request_id=key).status_code, 200)
        self.client.post('/chatbot/api/reset/')
        self.assertEqual(self.post(conversation, request_id=key).status_code, 200)
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 10)

    def test_duplicate_with_different_content_rejected(self):
        conversation = self.bootstrap()
        key = uuid4()
        self.post(conversation, request_id=key)
        response = self.post(conversation, content='Otra pregunta', request_id=key)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'request_id_conflict')
        self.assertEqual(ChatMessage.objects.count(), 2)

    def test_pending_duplicate_does_not_call_provider_or_reserve_twice(self):
        conversation = self.bootstrap()
        key = uuid4()
        self.reserve(conversation, key)
        with patch('chatbot.services.get_provider') as provider:
            response = self.post(conversation, content='Pendiente', request_id=key)
            self.assertEqual(response.status_code, 202)
            provider.assert_not_called()
        self.assertEqual(response.json()['status'], 'pending')
        self.assertEqual(response.json()['usage']['pending'], 1)
        self.assertEqual(self.post(conversation).status_code, 409)

    def test_pending_reservation_blocks_last_slot_in_new_conversation(self):
        conversation = self.bootstrap()
        ChatSessionUsage.objects.update(questions_consumed=9)
        message = self.reserve(conversation)
        new = self.client.post('/chatbot/api/reset/').json()
        self.assertEqual(new['usage']['remaining'], 0)
        self.assertEqual(self.post(new['conversation']['id']).status_code, 429)
        # Finishing an older conversation still consumes its reserved slot.
        result, status = finish_message(self.owner(), message.pk, 'Respuesta')
        self.assertEqual(status, 200)
        self.assertEqual(result['usage']['consumed'], 10)
        self.assertEqual(result['usage']['pending'], 0)

    def test_expired_reservation_reclaimed_and_late_answer_discarded(self):
        conversation = self.bootstrap()
        message = self.reserve(conversation)
        ChatMessage.objects.filter(pk=message.pk).update(reservation_expires_at=timezone.now() - timedelta(seconds=1))
        recovered = self.client.get('/chatbot/api/session/').json()
        self.assertEqual(recovered['usage']['remaining'], 10)
        self.assertEqual(recovered['messages'][0]['status'], 'failed')
        result, status = finish_message(self.owner(), message.pk, 'Too late')
        self.assertEqual(status, 502)
        self.assertEqual(result['usage']['consumed'], 0)
        self.assertFalse(ChatMessage.objects.filter(role='assistant').exists())

    def test_expiry_cleanup_is_idempotent_and_handles_authenticated_requests(self):
        conversation = self.bootstrap()
        message = self.reserve(conversation)
        user = get_user_model().objects.create_user(email='cleanup@example.test')
        owner = Owner(user_id=user.pk)
        authenticated_conversation = get_session(owner)['conversation']['id']
        reserve_message(owner, authenticated_conversation, uuid4(), 'Hello')
        ChatMessage.objects.update(reservation_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(reap_expired_reservations(), 2)
        self.assertEqual(reap_expired_reservations(), 0)
        call_command('expire_chatbot_reservations', verbosity=0)
        self.assertEqual(ChatSessionUsage.objects.get().pending_requests, 0)
        message.refresh_from_db()
        self.assertEqual(message.status, 'failed')

    def test_independent_anonymous_sessions_do_not_share_history_or_quota(self):
        conversation = self.bootstrap()
        self.post(conversation)
        other = Client()
        self.assertNotEqual(self.bootstrap(other), conversation)
        self.assertEqual(other.get('/chatbot/api/session/').json()['usage']['remaining'], 10)
        self.assertEqual(other.get('/chatbot/api/session/', {'conversation_id': conversation}).status_code, 404)
        self.assertEqual(self.post(conversation, client=other).status_code, 404)

    def test_authenticated_and_anonymous_ownership_are_strict(self):
        anonymous = self.bootstrap()
        first = get_user_model().objects.create_user(email='first@example.test')
        second = get_user_model().objects.create_user(email='second@example.test')
        self.client.force_login(first)
        authenticated = self.bootstrap()
        self.assertEqual(self.post(anonymous).status_code, 404)
        self.client.force_login(second)
        self.assertEqual(self.post(authenticated).status_code, 404)
        self.assertEqual(self.client.get('/chatbot/api/session/', {'conversation_id': authenticated}).status_code, 404)
        other = Client()
        self.bootstrap(other)
        self.assertEqual(self.post(authenticated, client=other).status_code, 404)

    def test_forged_session_cookie_cannot_select_another_conversation(self):
        conversation = self.bootstrap()
        forged = Client()
        forged.cookies[settings.SESSION_COOKIE_NAME] = 'a' * 32
        self.assertEqual(self.post(conversation, client=forged).status_code, 404)

    def test_replaying_valid_session_cookie_preserves_server_quota(self):
        conversation = self.bootstrap()
        previous_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        self.post(conversation)
        replay = Client()
        replay.cookies[settings.SESSION_COOKIE_NAME] = previous_cookie
        self.assertEqual(replay.get('/chatbot/api/session/').json()['usage']['remaining'], 9)

    def test_csrf_protected_for_both_anonymous_and_authenticated(self):
        for authenticated in (False, True):
            with self.subTest(authenticated=authenticated):
                client = Client(enforce_csrf_checks=True)
                if authenticated:
                    user = get_user_model().objects.create_user(email='csrf@example.test')
                    client.force_login(user)
                conversation = self.bootstrap(client)
                self.assertEqual(self.post(conversation, client=client).status_code, 403)
                self.assertEqual(client.post('/chatbot/api/reset/').status_code, 403)
                client.defaults['HTTP_X_CSRFTOKEN'] = client.cookies['csrftoken'].value
                self.assertEqual(self.post(conversation, client=client).status_code, 200)
                self.assertEqual(client.post('/chatbot/api/reset/').status_code, 200)

    def test_cross_origin_csrf_rejected(self):
        client = Client(enforce_csrf_checks=True)
        conversation = self.bootstrap(client)
        client.defaults.update(HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value,
                               HTTP_ORIGIN='https://evil.example')
        self.assertEqual(self.post(conversation, client=client).status_code, 403)

    def test_validation_does_not_reserve_quota(self):
        conversation = self.bootstrap()
        for content in ('', '  ', None, {}, 7, 'x' * 2001):
            with self.subTest(content_type=type(content)):
                self.assertEqual(self.post(conversation, content=content).status_code, 400)
        for data in ('{', '[]', 'null', '"hello"', '{}', '{"conversation_id": true}'):
            response = self.client.post('/chatbot/api/messages/', data=data, content_type='application/json')
            self.assertEqual(response.status_code, 400)
        response = self.client.post('/chatbot/api/messages/', data=b'\xff', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post('/chatbot/api/messages/', data='x' * 30000,
                                         content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/chatbot/api/messages/', data={}).status_code, 415)
        self.assertEqual(ChatMessage.objects.count(), 0)
        self.assertEqual(ChatSessionUsage.objects.get().pending_requests, 0)

    def test_invalid_and_unknown_conversation_identifiers(self):
        self.bootstrap()
        for value in ('bad', '', '1'):
            self.assertEqual(self.client.get('/chatbot/api/session/', {'conversation_id': value}).status_code, 400)
            self.assertEqual(self.post(value).status_code, 400)
        self.assertEqual(self.post(uuid4()).status_code, 404)

    @override_settings(CHATBOT_HISTORY_MESSAGES=3)
    def test_provider_history_bounded_completed_only_and_language_propagated(self):
        conversation = self.bootstrap()
        for i in range(3):
            self.post(conversation, content=f'Question {i}')
        with patch('chatbot.services.get_provider') as factory, patch('chatbot.services.get_language', return_value='es-es'):
            factory.return_value.respond.return_value = 'Answer'
            self.post(conversation, content='Last')
        args, kwargs = factory.return_value.respond.call_args
        self.assertEqual(args[0], [ChatTurn('user', 'Last')])
        self.assertEqual(kwargs['language'], 'es')
        self.assertTrue(kwargs['knowledge'].context)
        # Full persisted history is independent of the provider context window.
        self.assertEqual(ChatMessage.objects.count(), 8)

    def test_methods_restricted(self):
        for path, allowed in (('session', 'GET'), ('messages', 'POST'), ('reset', 'POST')):
            response = self.client.put(f'/chatbot/api/{path}/')
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response['Allow'], allowed)

    @override_settings(CHATBOT_ENABLED=False)
    def test_feature_flag_disables_all_endpoints(self):
        self.assertEqual(self.client.get('/chatbot/api/session/').status_code, 503)
        self.assertEqual(self.client.post('/chatbot/api/messages/').status_code, 503)
        self.assertEqual(self.client.post('/chatbot/api/reset/').status_code, 503)
        self.assertEqual(ChatConversation.objects.count(), 0)

    @override_settings(SESSION_ENGINE='django.contrib.sessions.backends.signed_cookies')
    def test_cookie_session_backend_rejected(self):
        self.assertEqual(self.client.get('/chatbot/api/session/').status_code, 503)
        self.assertEqual([e.id for e in chatbot_configuration(None)], ['chatbot.E001'])

    @override_settings(CHATBOT_ANONYMOUS_LIMIT=0)
    def test_invalid_settings_report_system_error(self):
        self.assertEqual([e.id for e in chatbot_configuration(None)], ['chatbot.E002'])

    def test_database_constraints_protect_ownership_and_idempotency(self):
        conversation = self.bootstrap()
        self.post(conversation)
        message = ChatMessage.objects.get(role='user')
        for kwargs in (
            {'anonymous_session_key': ''},
            {'anonymous_session_key': self.owner().session_key},
            {'anonymous_session_key': 'some-key', 'status': 'invalid'},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(IntegrityError), transaction.atomic():
                ChatConversation.objects.create(**kwargs)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChatMessage.objects.create(conversation_id=conversation, role='user', status='completed',
                                       content='Duplicate', request_id=message.request_id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChatSessionUsage.objects.filter(anonymous_session_key=self.owner().session_key).update(pending_requests=-1)
