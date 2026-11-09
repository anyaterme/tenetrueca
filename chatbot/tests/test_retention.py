import json
from datetime import timedelta
from io import StringIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from chatbot.models import ChatConversation, ChatMessage, ChatSessionUsage
from chatbot.retention import cleanup_history
from chatbot.services import Owner, get_session, reserve_message, send_message


@override_settings(CHATBOT_RETENTION_DAYS=30, CHATBOT_PROVIDER='mock', CHATBOT_RATE_LIMIT_REQUESTS=1000)
class RetentionTests(TestCase):
    def setUp(self):
        self.owner = Owner(session_key='retention-session')
        self.conversation = get_session(self.owner)['conversation']['id']
        self.old = timezone.now() - timedelta(days=31)

    def old_turn(self, owner=None, conversation=None):
        owner = owner or self.owner
        conversation = conversation or self.conversation
        request_id = uuid4()
        send_message(owner, conversation, request_id, 'Old private question')
        ChatMessage.objects.filter(conversation_id=conversation, request_id=request_id).update(created_at=self.old)
        return request_id

    def test_default_command_is_read_only_preview(self):
        self.old_turn()
        output = StringIO()
        with CaptureQueriesContext(connection) as captured:
            call_command('cleanup_chatbot_history', stdout=output)
        result = json.loads(output.getvalue())
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['messages'], 2)
        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
                             for q in captured.captured_queries))
        self.assertNotIn('private', output.getvalue())
        self.assertNotIn(self.owner.session_key, output.getvalue())

    def test_deletes_old_turns_without_restoring_active_session_quota(self):
        self.old_turn()
        Session.objects.create(session_key=self.owner.session_key, session_data='',
                               expire_date=timezone.now()+timedelta(days=60))
        ChatConversation.objects.update(updated_at=self.old)
        ChatSessionUsage.objects.update(updated_at=self.old)
        result = cleanup_history(execute=True, batch_size=1)
        self.assertEqual((result['messages'], result['conversations'], result['usage_rows']), (2, 1, 0))
        self.assertEqual(ChatMessage.objects.count(), 0)
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 1)
        self.assertEqual(get_session(self.owner)['usage']['remaining'], 9)

    def test_recent_turns_and_recent_activity_metadata_are_kept(self):
        old_id = self.old_turn()
        send_message(self.owner, self.conversation, uuid4(), 'Current question')
        result = cleanup_history(execute=True)
        self.assertEqual(result['messages'], 2)
        self.assertEqual(result['conversations'], 0)
        self.assertFalse(ChatMessage.objects.filter(request_id=old_id).exists())
        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 2)

    def test_turn_straddling_cutoff_is_kept_as_a_pair(self):
        key = self.old_turn()
        ChatMessage.objects.filter(request_id=key, role='assistant').update(created_at=timezone.now())
        self.assertEqual(cleanup_history(execute=True)['messages'], 0)
        self.assertEqual(ChatMessage.objects.count(), 2)

    def test_live_pending_conversation_is_not_deleted(self):
        self.old_turn()
        pending, _, _ = reserve_message(self.owner, self.conversation, uuid4(), 'Pending')
        ChatMessage.objects.filter(pk=pending.pk).update(created_at=self.old)
        ChatConversation.objects.update(updated_at=self.old)
        result = cleanup_history(execute=True)
        self.assertEqual(result['messages'], 0)
        self.assertEqual(result['conversations'], 0)
        self.assertEqual(ChatSessionUsage.objects.get().pending_requests, 1)

    def test_expired_reservation_is_released_before_deleting_old_history(self):
        pending, _, _ = reserve_message(self.owner, self.conversation, uuid4(), 'Pending')
        ChatMessage.objects.filter(pk=pending.pk).update(created_at=self.old,
            reservation_expires_at=timezone.now()-timedelta(seconds=1))
        result = cleanup_history(execute=True)
        self.assertEqual((result['messages'], result['expired_reservations']), (1, 1))
        self.assertEqual(ChatSessionUsage.objects.get().pending_requests, 0)
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 0)

    def test_stale_usage_removed_only_without_sessions_or_conversations(self):
        self.old_turn()
        ChatConversation.objects.update(updated_at=self.old)
        ChatSessionUsage.objects.update(updated_at=self.old)
        result = cleanup_history(execute=True)
        self.assertEqual((result['messages'], result['conversations'], result['usage_rows']), (2, 1, 1))
        self.assertFalse(ChatSessionUsage.objects.exists())
        self.assertEqual(cleanup_history(execute=True)['messages'], 0)

    def test_existing_expired_django_session_row_also_preserves_quota_metadata(self):
        self.old_turn()
        Session.objects.create(session_key=self.owner.session_key, session_data='', expire_date=self.old)
        ChatConversation.objects.update(updated_at=self.old)
        ChatSessionUsage.objects.update(updated_at=self.old)
        cleanup_history(execute=True)
        self.assertEqual(ChatSessionUsage.objects.get().questions_consumed, 1)
        self.assertTrue(Session.objects.filter(session_key=self.owner.session_key).exists())

    def test_authenticated_history_is_pruned_without_deleting_user(self):
        user = get_user_model().objects.create_user(email='retention-auth@example.test')
        owner = Owner(user_id=user.pk)
        conversation = get_session(owner)['conversation']['id']
        self.old_turn(owner, conversation)
        ChatConversation.objects.filter(pk=conversation).update(updated_at=self.old)
        result = cleanup_history(execute=True)
        self.assertEqual(result['messages'], 2)
        self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())
        self.assertFalse(ChatConversation.objects.filter(pk=conversation).exists())

    @override_settings(CHATBOT_RETENTION_DAYS=60)
    def test_retention_is_configurable(self):
        self.old_turn()
        self.assertEqual(cleanup_history(execute=True)['messages'], 0)

    @override_settings(CHATBOT_ENABLED=False)
    def test_maintenance_works_when_chatbot_is_disabled(self):
        self.old_turn()
        self.assertEqual(cleanup_history(execute=True)['messages'], 2)

    def test_invalid_settings_and_batch_are_rejected(self):
        for batch in (0, 501):
            with self.assertRaises(CommandError):
                call_command('cleanup_chatbot_history', batch_size=batch, stdout=StringIO())
        with override_settings(CHATBOT_RETENTION_DAYS=0), self.assertRaises(CommandError):
            call_command('cleanup_chatbot_history', stdout=StringIO())
