from django.test import TestCase

from audit.models import AuditEvent


class AuditEventTests(TestCase):
    def test_audit_event_can_store_safe_metadata(self):
        event = AuditEvent.objects.create(
            action='health.checked',
            entity='system',
            entity_id='health',
            metadata={'origin': 'test'},
        )

        self.assertEqual(str(event), 'health.checked system:health')
