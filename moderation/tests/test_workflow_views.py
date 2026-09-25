from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCenterAccess
from audit.models import AuditEvent
from catalog.models import Category
from core.roles import ROLE_MODERATOR
from inventory.models import InventoryItem
from locations.models import RecyclingCenter
from moderation.models import ModerationDecision
from publications.models import Publication


class ModerationWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.submitter = User.objects.create_user(
            email='citizen-moderation@example.com',
            username='citizen-moderation',
            first_name='Citizen',
            password='test-password',
        )
        cls.moderator = User.objects.create_user(
            email='moderator@example.com',
            username='moderator',
            first_name='Moderator',
            password='test-password',
        )
        moderator_group = Group.objects.create(name=ROLE_MODERATOR)
        cls.moderator.groups.add(moderator_group)
        cls.operator = User.objects.create_user(
            email='operator-moderation@example.com',
            username='operator-moderation',
            first_name='Operator',
            password='test-password',
        )
        cls.center = RecyclingCenter.objects.create(
            name='Punto Limpio Norte',
            slug='punto-limpio-norte',
        )
        cls.submitter.habitual_recycling_center = cls.center
        cls.submitter.save(update_fields=['habitual_recycling_center'])
        UserCenterAccess.objects.create(
            user=cls.operator,
            center=cls.center,
            role=UserCenterAccess.ScopeRole.OPERATOR,
        )
        cls.category = Category.objects.create(name='Hogar', slug='hogar')

    def create_publication(self, title='Objeto pendiente', submitted_at=None):
        return Publication.objects.create(
            submitter=self.submitter,
            title=title,
            description='Descripción suficiente para la moderación.',
            category=self.category,
            status=Publication.Status.PENDING_REVIEW,
            submitted_at=submitted_at or timezone.now(),
        )

    def test_moderator_can_filter_queue_by_status_date_and_center(self):
        visible = self.create_publication()
        self.create_publication(
            title='Publicación antigua',
            submitted_at=timezone.now() - timedelta(days=10),
        )
        self.client.force_login(self.moderator)

        response = self.client.get(
            reverse('moderation:queue'),
            {
                'status': Publication.Status.PENDING_REVIEW,
                'center': self.center.slug,
                'date_from': timezone.localdate().isoformat(),
                'ordering': 'newest',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['page_obj'].object_list), [visible])

    def test_operator_cannot_open_or_decide_moderation(self):
        publication = self.create_publication()
        self.client.force_login(self.operator)

        self.assertEqual(self.client.get(reverse('moderation:queue')).status_code, 403)
        response = self.client.post(
            reverse('moderation:decide', args=[publication.pk]),
            {'decision': ModerationDecision.Decision.APPROVE, 'notes': ''},
        )
        self.assertEqual(response.status_code, 403)
        publication.refresh_from_db()
        self.assertEqual(publication.status, Publication.Status.PENDING_REVIEW)

    def test_reject_and_request_changes_require_a_reason(self):
        publication = self.create_publication()
        self.client.force_login(self.moderator)

        for decision in (
            ModerationDecision.Decision.REJECT,
            ModerationDecision.Decision.REQUEST_CHANGES,
        ):
            with self.subTest(decision=decision):
                response = self.client.post(
                    reverse('moderation:decide', args=[publication.pk]),
                    {'decision': decision, 'notes': ''},
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Indica un motivo para esta decisión.')
                self.assertFalse(ModerationDecision.objects.exists())

    def test_approval_records_human_decision_and_audit_without_inventory(self):
        publication = self.create_publication()
        self.client.force_login(self.moderator)

        response = self.client.post(
            reverse('moderation:decide', args=[publication.pk]),
            {'decision': ModerationDecision.Decision.APPROVE, 'notes': 'Contenido correcto.'},
        )

        self.assertRedirects(response, reverse('moderation:detail', args=[publication.pk]))
        publication.refresh_from_db()
        self.assertEqual(publication.status, Publication.Status.APPROVED)
        self.assertIsNotNone(publication.approved_at)
        decision = publication.moderation_decisions.get()
        self.assertEqual(decision.reviewer, self.moderator)
        self.assertEqual(decision.decision, ModerationDecision.Decision.APPROVE)
        self.assertFalse(InventoryItem.objects.filter(publication=publication).exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                actor=self.moderator,
                action='publication.moderation.approve',
                entity_id=str(publication.pk),
            ).exists()
        )

    def test_request_changes_preserves_reason_for_citizen(self):
        publication = self.create_publication()
        self.client.force_login(self.moderator)

        self.client.post(
            reverse('moderation:decide', args=[publication.pk]),
            {
                'decision': ModerationDecision.Decision.REQUEST_CHANGES,
                'notes': 'Añade una fotografía del lateral.',
            },
        )

        publication.refresh_from_db()
        self.assertEqual(publication.status, Publication.Status.CHANGES_REQUESTED)
        self.assertEqual(
            publication.moderation_decisions.get().notes,
            'Añade una fotografía del lateral.',
        )
