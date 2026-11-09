from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from chatbot.models import ChatConversation, ChatMessage, ChatSessionUsage
from chatbot.tests.fixtures import ProviderEvidenceMixin
from chatbot.knowledge import KnowledgeService
from chatbot.services import (ChatError, Owner, finish_message, get_session,
                              reserve_message, reset_conversation, send_message)


@override_settings(CHATBOT_ENABLED=True)
class ProviderTransactionTests(ProviderEvidenceMixin, TransactionTestCase):
    def test_retrieval_and_reference_revalidation_run_outside_transactions(self):
        original = KnowledgeService().retrieve
        def retrieve(question, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(ChatSessionUsage.objects.get().pending_requests, 1)
            return original(question, **kwargs)
        owner = Owner(session_key='knowledge-transaction-check')
        conversation = get_session(owner)['conversation']['id']
        with patch('chatbot.answering.KnowledgeService.retrieve', side_effect=retrieve) as recorded:
            result, status = send_message(owner, conversation, uuid4(), 'Hello', provider=Mock(respond=Mock(return_value='Answer')))
        self.assertEqual(status, 200)
        self.assertEqual(recorded.call_count, 2)

    def test_provider_runs_outside_transactions(self):
        owner = Owner(session_key='transaction-check')
        conversation = get_session(owner)['conversation']['id']

        def respond(history, *, language, knowledge=None):
            self.assertFalse(connection.in_atomic_block)
            usage = ChatSessionUsage.objects.get()
            self.assertEqual((usage.questions_consumed, usage.pending_requests), (0, 1))
            return 'Answer'

        result, status = send_message(owner, conversation, uuid4(), 'Hi', provider=Mock(respond=respond))
        self.assertEqual(status, 200)
        self.assertEqual(result['usage']['consumed'], 1)

    def test_http_view_stays_outside_atomic_requests(self):
        # Exercise the actual Django request handler with per-request transactions enabled.
        with patch.dict(connection.settings_dict, ATOMIC_REQUESTS=True):
            conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']

            def respond(history, *, language, knowledge=None):
                self.assertFalse(connection.in_atomic_block)
                return 'Answer'

            with patch('chatbot.services.get_provider', return_value=Mock(respond=respond)):
                response = self.client.post('/chatbot/api/messages/', {
                    'conversation_id': conversation, 'request_id': str(uuid4()), 'content': 'Hello',
                }, content_type='application/json')
        self.assertEqual(response.status_code, 200)


@skipUnless(connection.vendor == 'postgresql', 'Requires actual PostgreSQL row locks.')
@override_settings(CHATBOT_ENABLED=True, CHATBOT_ANONYMOUS_LIMIT=10)
class ChatbotConcurrencyTests(ProviderEvidenceMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.owner = Owner(session_key='shared-server-session')
        self.conversation = get_session(self.owner)['conversation']['id']

    def threaded(self, function):
        close_old_connections()
        try:
            return function()
        finally:
            connection.close()

    def race(self, functions):
        barrier = Barrier(len(functions))

        def run(function):
            barrier.wait(timeout=10)
            return function()

        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            futures = [pool.submit(self.threaded, lambda f=f: run(f)) for f in functions]
            return [future.result(timeout=20) for future in futures]

    def attempt(self, owner, conversation, key=None, provider=None):
        try:
            return send_message(owner, conversation, key or uuid4(), 'Hello', provider=provider)[1]
        except ChatError as error:
            return error.status

    def test_racing_last_slot_cannot_exceed_quota(self):
        ChatSessionUsage.objects.update(questions_consumed=9)
        results = self.race([lambda: self.attempt(self.owner, self.conversation) for _ in range(4)])
        self.assertEqual(results.count(200), 1)
        self.assertTrue(all(status in (200, 409, 429) for status in results))
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (10, 0))
        self.assertEqual(ChatMessage.objects.filter(role='assistant').count(), 1)

    def test_duplicate_requests_invoke_provider_once(self):
        key = uuid4()
        provider = Mock()
        provider.respond.return_value = 'Answer'
        results = self.race([lambda: self.attempt(self.owner, self.conversation, key, provider) for _ in range(4)])
        self.assertIn(200, results)
        self.assertTrue(all(status in (200, 202) for status in results))
        provider.respond.assert_called_once()
        self.assertEqual(ChatMessage.objects.count(), 2)
        persisted = ChatMessage.objects.get(role='assistant')
        self.assertEqual(persisted.sources[0]['source_id'], f'faq:{self.provider_faq.pk}')
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (1, 0))

    def test_concurrent_duplicates_retrieve_and_revalidate_only_for_claimed_turn(self):
        key = uuid4()
        provider = Mock()
        provider.respond.return_value = 'Answer'
        with patch('chatbot.answering.KnowledgeService.retrieve', wraps=KnowledgeService().retrieve) as retrieval:
            results = self.race([lambda: self.attempt(self.owner, self.conversation, key, provider) for _ in range(12)])
        self.assertTrue(all(status in (200, 202) for status in results))
        self.assertEqual(retrieval.call_count, 2)
        provider.respond.assert_called_once()

    def test_concurrent_finalization_consumes_reservation_only_once(self):
        message, _, _ = reserve_message(self.owner, self.conversation, uuid4(), 'Question')
        results = self.race([lambda: finish_message(self.owner, message.pk, 'Answer')[1] for _ in range(3)])
        self.assertEqual(results, [200, 200, 200])
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (1, 0))
        self.assertEqual(ChatMessage.objects.filter(role='assistant').count(), 1)

    def test_concurrent_reference_finalization_is_atomic_and_idempotent(self):
        from chatbot.answering import ConversationAnswerer
        from chatbot.providers import SimulatedProvider
        reply = ConversationAnswerer().answer('Hello', language='es', provider_factory=SimulatedProvider)
        message, _, _ = reserve_message(self.owner, self.conversation, uuid4(), 'Hello')
        results = self.race([lambda: finish_message(self.owner, message.pk, reply) for _ in range(4)])
        self.assertTrue(all(status == 200 for _, status in results))
        self.assertTrue(all(result['messages'][-1]['sources'][0]['available'] for result, _ in results))
        saved = ChatMessage.objects.get(role='assistant')
        self.assertEqual(len(saved.sources), 1)
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 1)

    def test_first_session_usage_and_conversation_creation_is_atomic(self):
        owner = Owner(session_key='new-server-session')
        results = self.race([lambda: get_session(owner)['conversation']['id'] for _ in range(3)])
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(ChatSessionUsage.objects.filter(anonymous_session_key=owner.session_key).count(), 1)
        self.assertEqual(ChatConversation.objects.filter(anonymous_session_key=owner.session_key).count(), 1)

    def test_first_authenticated_conversation_creation_is_atomic(self):
        user = get_user_model().objects.create_user(email='concurrent@example.test')
        owner = Owner(user_id=user.pk)
        results = self.race([lambda: get_session(owner)['conversation']['id'] for _ in range(3)])
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(ChatConversation.objects.filter(user=user).count(), 1)

    def test_different_sessions_have_independent_capacity(self):
        other = Owner(session_key='different-server-session')
        second = get_session(other)['conversation']['id']
        results = self.race([lambda: self.attempt(self.owner, self.conversation), lambda: self.attempt(other, second)])
        self.assertEqual(results, [200, 200])
        self.assertEqual(list(ChatSessionUsage.objects.values_list('questions_consumed', flat=True)), [1, 1])

    def test_reset_while_provider_waits_keeps_reservation_without_holding_locks(self):
        ChatSessionUsage.objects.update(questions_consumed=9)
        entered, release = Event(), Event()

        def respond(history, *, language, knowledge=None):
            self.assertFalse(connection.in_atomic_block)
            entered.set()
            if not release.wait(timeout=10):
                raise RuntimeError('Test provider timed out')
            return 'Answer'

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.threaded, lambda: self.attempt(
                self.owner, self.conversation, provider=Mock(respond=respond)))
            try:
                self.assertTrue(entered.wait(timeout=10))
                new = reset_conversation(self.owner)
                self.assertEqual(new['usage']['pending'], 1)
                self.assertEqual(new['usage']['remaining'], 0)
                self.assertEqual(self.attempt(self.owner, new['conversation']['id']), 429)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=15), 200)
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (10, 0))


    @override_settings(CHATBOT_RATE_LIMIT_REQUESTS=1, CHATBOT_RATE_LIMIT_WINDOW_SECONDS=60)
    def test_concurrent_frequency_limit_serializes_all_workers(self):
        results = self.race([lambda: self.attempt(self.owner, self.conversation) for _ in range(4)])
        self.assertEqual(results.count(200), 1)
        self.assertTrue(all(status in (200, 409, 429) for status in results))
        self.assertEqual(ChatMessage.objects.filter(role='user').count(), 1)
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 1)


    def test_load_24_duplicate_requests_call_provider_once(self):
        key = uuid4()
        provider = Mock()
        provider.respond.return_value = 'Answer'
        results = self.race([lambda: self.attempt(self.owner, self.conversation, key, provider) for _ in range(24)])
        self.assertTrue(all(status in (200, 202) for status in results))
        provider.respond.assert_called_once()
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 1)
        self.assertEqual(ChatMessage.objects.count(), 2)

    def test_load_24_sessions_have_independent_histories_and_quota(self):
        owners = [Owner(session_key=f'audit-independent-{i}') for i in range(24)]
        def run(owner):
            conversation = get_session(owner)['conversation']['id']
            return self.attempt(owner, conversation)
        results = self.race([lambda owner=owner: run(owner) for owner in owners])
        self.assertEqual(results, [200] * 24)
        usages = ChatSessionUsage.objects.filter(anonymous_session_key__startswith='audit-independent-')
        self.assertEqual(usages.count(), 24)
        self.assertTrue(all(value == (1, 0) for value in usages.values_list('questions_consumed', 'pending_requests')))

    def test_load_24_last_slot_requests_never_exceed_ten(self):
        ChatSessionUsage.objects.update(questions_consumed=9)
        results = self.race([lambda: self.attempt(self.owner, self.conversation) for _ in range(24)])
        self.assertEqual(results.count(200), 1)
        self.assertTrue(all(status in (200, 409, 429) for status in results))
        usage = ChatSessionUsage.objects.get()
        self.assertEqual((usage.questions_consumed, usage.pending_requests), (10, 0))

    def test_slow_response_with_12_duplicate_tabs_keeps_one_reservation(self):
        entered, release = Event(), Event()
        provider = Mock()
        def respond(history, *, language, knowledge=None):
            entered.set()
            if not release.wait(timeout=15):
                raise RuntimeError('local test timeout')
            return 'Answer'
        provider.respond.side_effect = respond
        key = uuid4()
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.threaded, lambda: self.attempt(self.owner, self.conversation, key, provider))
            try:
                self.assertTrue(entered.wait(timeout=10))
                results = self.race([lambda: self.attempt(self.owner, self.conversation, key, provider) for _ in range(12)])
                self.assertEqual(results, [202] * 12)
                usage = ChatSessionUsage.objects.get()
                self.assertEqual((usage.questions_consumed, usage.pending_requests), (0, 1))
            finally:
                release.set()
            self.assertEqual(first.result(timeout=20), 200)
        provider.respond.assert_called_once()

    def test_parallel_provider_failures_release_all_independent_reservations(self):
        owners = [Owner(session_key=f'audit-failure-{i}') for i in range(12)]
        provider = Mock()
        provider.respond.side_effect = RuntimeError('simulated provider failure')
        def run(owner):
            conversation = get_session(owner)['conversation']['id']
            return self.attempt(owner, conversation, provider=provider)
        results = self.race([lambda owner=owner: run(owner) for owner in owners])
        self.assertEqual(results, [502] * 12)
        usages = ChatSessionUsage.objects.filter(anonymous_session_key__startswith='audit-failure-')
        self.assertTrue(all(value == (0, 0) for value in usages.values_list('questions_consumed', 'pending_requests')))

    def test_parallel_retention_workers_do_not_recreate_deleted_usage(self):
        from datetime import timedelta
        from django.utils import timezone
        from chatbot.retention import cleanup_history
        old = timezone.now() - timedelta(days=31)
        self.attempt(self.owner, self.conversation)
        ChatMessage.objects.update(created_at=old)
        ChatConversation.objects.update(updated_at=old)
        ChatSessionUsage.objects.update(updated_at=old)
        self.race([lambda: cleanup_history(execute=True, batch_size=1) for _ in range(3)])
        self.assertEqual(ChatMessage.objects.count(), 0)
        self.assertEqual(ChatConversation.objects.count(), 0)
        self.assertEqual(ChatSessionUsage.objects.count(), 0)
