from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCenterAccess
from audit.models import AuditEvent
from backoffice.center_filter import ADMIN_CENTER_FILTER_SESSION_KEY
from catalog.models import Category, ReusableObject
from core.permissions import scope_publications
from core.roles import ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER
from inventory.models import InventoryItem
from locations.models import RecyclingCenter
from publications.models import Publication
from reservations.models import Reservation


class AdminCenterFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        admin_group, _ = Group.objects.get_or_create(name=ROLE_STAFF_ADMIN)
        manager_group, _ = Group.objects.get_or_create(name=ROLE_STAFF_MANAGER)
        cls.center_a = RecyclingCenter.objects.create(
            name='Punto Limpio de Adeje', slug='filter-adeje'
        )
        cls.center_b = RecyclingCenter.objects.create(
            name='Punto Limpio de Arona', slug='filter-arona'
        )
        cls.center_c = RecyclingCenter.objects.create(
            name='Punto Limpio de La Orotava', slug='filter-orotava'
        )
        cls.admin = User.objects.create_user(
            email='filter-admin@example.com',
            password='test-password',
            first_name='Admin',
            is_staff=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.admin.groups.add(admin_group)
        cls.manager = User.objects.create_user(
            email='filter-manager@example.com',
            password='test-password',
            first_name='Gestora',
            is_staff=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.manager.groups.add(manager_group)
        UserCenterAccess.objects.create(
            user=cls.manager,
            center=cls.center_a,
            role=UserCenterAccess.ScopeRole.SUPERVISOR,
        )
        cls.citizen_a = User.objects.create_user(
            email='filter-a@example.com',
            password='test-password',
            habitual_recycling_center=cls.center_a,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.citizen_b = User.objects.create_user(
            email='filter-b@example.com',
            password='test-password',
            habitual_recycling_center=cls.center_b,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.category = Category.objects.create(name='Filtro', slug='filter-category')
        cls.pending_a = cls._publication(
            cls.citizen_a, 'Publicación pendiente Adeje', Publication.Status.PENDING_REVIEW
        )
        cls.pending_b = cls._publication(
            cls.citizen_b, 'Publicación pendiente Arona', Publication.Status.PENDING_REVIEW
        )
        cls.approved_a = cls._publication(
            cls.citizen_a, 'Recepción pendiente Adeje', Publication.Status.APPROVED
        )
        cls.approved_b = cls._publication(
            cls.citizen_b, 'Recepción pendiente Arona', Publication.Status.APPROVED
        )
        pickup_a = cls._publication(
            cls.citizen_a, 'Objeto para recoger Adeje', Publication.Status.APPROVED
        )
        pickup_b = cls._publication(
            cls.citizen_b, 'Objeto para recoger Arona', Publication.Status.APPROVED
        )
        cls.item_a = cls._item(pickup_a, cls.center_a, 'FILTER-A')
        cls.item_b = cls._item(pickup_b, cls.center_b, 'FILTER-B')
        requester = User.objects.create_user(
            email='filter-requester@example.com',
            password='test-password',
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.reservation_a = Reservation.objects.create(
            inventory_item=cls.item_a,
            user=requester,
            expires_at=timezone.now() + timedelta(days=1),
        )
        cls.reservation_b = Reservation.objects.create(
            inventory_item=cls.item_b,
            user=requester,
            expires_at=timezone.now() + timedelta(days=1),
        )

    @classmethod
    def _publication(cls, submitter, title, status):
        now = timezone.now()
        return Publication.objects.create(
            submitter=submitter,
            title=title,
            description=title,
            category=cls.category,
            status=status,
            submitted_at=now - timedelta(hours=2),
            approved_at=now - timedelta(hours=1)
            if status == Publication.Status.APPROVED
            else None,
        )

    @classmethod
    def _item(cls, publication, center, reference):
        now = timezone.now()
        return InventoryItem.objects.create(
            reference=reference,
            owner=publication.submitter,
            publication=publication,
            center=center,
            title=publication.title,
            description=publication.description,
            category=cls.category.name,
            category_node=cls.category,
            condition=ReusableObject.Condition.GOOD,
            status=ReusableObject.Status.AVAILABLE,
            received_at=now,
            validated_at=now,
            validated_by=cls.admin,
        )

    def login(self, user=None):
        self.client.force_login(user or self.admin)

    def apply(self, *centers, next_url=None):
        return self.client.post(
            reverse('backoffice:update_center_filter'),
            {
                'center_ids': [center.pk for center in centers],
                'next': next_url or reverse('backoffice:dashboard'),
            },
        )

    def test_01_administrator_sees_selector(self):
        self.login()
        response = self.client.get(reverse('backoffice:dashboard'))
        self.assertContains(response, 'data-center-filter-toggle')

    def test_02_default_scope_is_all_centers(self):
        self.login()
        response = self.client.get(reverse('backoffice:dashboard'))
        self.assertContains(response, 'Todos los centros')
        self.assertTrue(response.context['admin_center_filter'].is_all)

    def test_03_administrator_can_select_one_center(self):
        self.login()
        self.assertRedirects(self.apply(self.center_a), reverse('backoffice:dashboard'))
        response = self.client.get(reverse('backoffice:dashboard'))
        self.assertEqual(response.context['admin_center_filter'].center_ids, (self.center_a.pk,))
        self.assertContains(response, self.center_a.name)

    def test_04_administrator_can_select_multiple_centers(self):
        self.login()
        self.apply(self.center_a, self.center_b)
        response = self.client.get(reverse('backoffice:dashboard'))
        self.assertEqual(
            set(response.context['admin_center_filter'].center_ids),
            {self.center_a.pk, self.center_b.pk},
        )
        self.assertContains(response, '2 centros seleccionados')

    def test_05_empty_selection_is_rejected(self):
        self.login()
        response = self.apply()
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(ADMIN_CENTER_FILTER_SESSION_KEY, self.client.session)

    def test_06_clear_returns_to_all_centers(self):
        self.login()
        self.apply(self.center_a)
        response = self.client.post(
            reverse('backoffice:update_center_filter'),
            {'clear': '1', 'next': reverse('backoffice:dashboard')},
        )
        self.assertRedirects(response, reverse('backoffice:dashboard'))
        self.assertNotIn(ADMIN_CENTER_FILTER_SESSION_KEY, self.client.session)

    def test_07_selection_is_kept_while_navigating(self):
        self.login()
        self.apply(self.center_a)
        for url in (
            reverse('backoffice:dashboard'),
            reverse('moderation:queue'),
            reverse('inventory:reception_queue'),
            reverse('backoffice:pickup_queue'),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.context['admin_center_filter'].center_ids, (self.center_a.pk,))

    def test_08_dashboard_kpis_respect_filter(self):
        self.login()
        self.apply(self.center_a)
        response = self.client.get(reverse('backoffice:dashboard'))
        metrics = {metric['label']: metric['value'] for metric in response.context['metrics']}
        self.assertEqual(metrics['Por moderar'], 1)
        self.assertEqual(metrics['Pendientes de recepción'], 1)
        self.assertEqual(metrics['Recogidas activas'], 1)

    def test_09_pending_work_respects_filter(self):
        self.login()
        self.apply(self.center_a)
        response = self.client.get(reverse('backoffice:dashboard'))
        task_titles = {task['title'] for task in response.context['tasks']}
        self.assertIn(self.pending_a.title, task_titles)
        self.assertNotIn(self.pending_b.title, task_titles)

    def test_10_moderation_queue_respects_filter(self):
        self.login()
        self.apply(self.center_a)
        response = self.client.get(reverse('moderation:queue'))
        self.assertContains(response, self.pending_a.title)
        self.assertNotContains(response, self.pending_b.title)

    def test_11_reception_queue_respects_filter(self):
        self.login()
        self.apply(self.center_a)
        response = self.client.get(reverse('inventory:reception_queue'))
        self.assertContains(response, self.approved_a.title)
        self.assertNotContains(response, self.approved_b.title)

    def test_12_pickup_queue_respects_filter(self):
        self.login()
        self.apply(self.center_a)
        response = self.client.get(reverse('backoffice:pickup_queue'))
        self.assertContains(response, self.item_a.title)
        self.assertNotContains(response, self.item_b.title)

    def test_13_filter_does_not_change_authorization_scope(self):
        self.login()
        self.apply(self.center_a)
        authorized = scope_publications(self.admin, Publication.objects.all())
        self.assertTrue(authorized.filter(pk=self.pending_b.pk).exists())

    def test_14_admin_can_open_resource_outside_filter_directly(self):
        self.login()
        self.apply(self.center_a)
        response = self.client.get(reverse('moderation:detail', args=[self.pending_b.pk]))
        self.assertEqual(response.status_code, 200)

    def test_15_invalid_center_ids_are_rejected(self):
        self.login()
        response = self.client.post(
            reverse('backoffice:update_center_filter'),
            {'center_ids': [self.center_a.pk, 999999]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(ADMIN_CENTER_FILTER_SESSION_KEY, self.client.session)

    def test_16_manager_does_not_see_selector(self):
        self.login(self.manager)
        response = self.client.get(reverse('backoffice:dashboard'))
        self.assertNotContains(response, 'data-center-filter-toggle')

    def test_17_manager_sees_assigned_center_name(self):
        self.login(self.manager)
        response = self.client.get(reverse('backoffice:dashboard'))
        self.assertContains(response, self.center_a.name)

    def test_18_manager_context_has_no_chevron(self):
        self.login(self.manager)
        response = self.client.get(reverse('backoffice:dashboard'))
        self.assertNotContains(response, 'staff-center-summary__chevron')

    def test_19_manager_cannot_call_filter_endpoint(self):
        self.login(self.manager)
        response = self.apply(self.center_b)
        self.assertEqual(response.status_code, 403)

    def test_20_manager_authorization_remains_limited(self):
        self.login(self.manager)
        response = self.client.get(reverse('moderation:detail', args=[self.pending_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_21_operational_activity_respects_filter(self):
        event_a = AuditEvent.objects.create(
            actor=self.admin,
            action='operation.center.a',
            entity='Test',
            entity_id='a',
            source='backoffice',
            metadata={'center_id': self.center_a.pk},
        )
        event_b = AuditEvent.objects.create(
            actor=self.admin,
            action='operation.center.b',
            entity='Test',
            entity_id='b',
            source='backoffice',
            metadata={'center_id': self.center_b.pk},
        )
        self.login()
        self.apply(self.center_a)
        activity = self.client.get(reverse('backoffice:dashboard')).context['recent_activity']
        self.assertIn(event_a, activity)
        self.assertNotIn(event_b, activity)

    def test_22_global_activity_remains_visible(self):
        event = AuditEvent.objects.create(
            actor=self.admin,
            action='staff.permissions.changed',
            entity='User',
            entity_id='1',
            source='backoffice',
        )
        self.login()
        self.apply(self.center_a)
        activity = self.client.get(reverse('backoffice:dashboard')).context['recent_activity']
        self.assertIn(event, activity)

    def test_23_session_value_persists_between_views(self):
        self.login()
        self.apply(self.center_a, self.center_b)
        self.client.get(reverse('moderation:queue'))
        self.client.get(reverse('inventory:reception_queue'))
        self.assertEqual(
            set(self.client.session[ADMIN_CENTER_FILTER_SESSION_KEY]),
            {self.center_a.pk, self.center_b.pk},
        )

    def test_24_clear_removes_session_value(self):
        self.login()
        self.apply(self.center_a)
        self.client.post(reverse('backoffice:update_center_filter'), {'clear': '1'})
        self.assertNotIn(ADMIN_CENTER_FILTER_SESSION_KEY, self.client.session)

    def test_25_admin_session_filter_is_ignored_for_manager(self):
        self.login(self.manager)
        session = self.client.session
        session[ADMIN_CENTER_FILTER_SESSION_KEY] = [self.center_b.pk]
        session.save()
        response = self.client.get(reverse('moderation:queue'))
        self.assertContains(response, self.pending_a.title)
        self.assertNotContains(response, self.pending_b.title)
        self.assertFalse(response.context['admin_center_filter'].is_administrator)
