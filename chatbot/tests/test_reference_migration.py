"""Exercise the real additive migration only in the isolated migrated test database."""
from unittest import skipUnless
from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


@skipUnless(connection.vendor == 'postgresql', 'Requires isolated migrated PostgreSQL test settings.')
class ReferenceMigrationTests(TransactionTestCase):
    def test_additive_json_field_preserves_old_messages(self):
        before = [('chatbot', '0001_initial')]
        after = [('chatbot', '0002_chatmessage_sources')]
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes('chatbot')
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            conversation = apps.get_model('chatbot', 'ChatConversation').objects.create(
                anonymous_session_key='synthetic-pre-migration', status='active')
            message = apps.get_model('chatbot', 'ChatMessage').objects.create(
                conversation=conversation, role='assistant', content='Historial anterior conservado',
                status='completed', request_id=uuid4())
            pk = message.pk
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(leaves)
        restored = executor.loader.project_state(after).apps.get_model('chatbot', 'ChatMessage').objects.get(pk=pk)
        self.assertEqual(restored.content, 'Historial anterior conservado')
        self.assertEqual(restored.sources, [])
        # The persistent database default also permits an old deployed writer during rollback.
        old_writer = apps.get_model('chatbot', 'ChatMessage').objects.create(
            conversation=conversation, role='assistant', content='Escritor anterior compatible',
            status='completed', request_id=uuid4())
        current = executor.loader.project_state(after).apps.get_model('chatbot', 'ChatMessage')
        self.assertEqual(current.objects.get(pk=old_writer.pk).sources, [])
