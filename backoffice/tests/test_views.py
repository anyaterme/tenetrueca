from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCenterAccess
from catalog.models import Category, ReusableObject
from core.roles import ROLE_MODERATOR
from inventory.models import InventoryItem
from locations.models import RecyclingCenter
from publications.models import Publication
from reservations.models import Reservation


class BackofficeViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.citizen = User.objects.create_user(
            email='citizen-backoffice@example.com',
            first_name='Ciudadana',
            password='test-password',
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.staff = User.objects.create_user(
            email='staff-backoffice@example.com',
            first_name='Staff',
            password='test-password',
            is_staff=True,
        )
        cls.moderator = User.objects.create_user(
            email='moderator-backoffice@example.com',
            first_name='Marta',
            password='test-password',
        )
        moderator_group = Group.objects.create(name=ROLE_MODERATOR)
        cls.moderator.groups.add(moderator_group)
        cls.operator = User.objects.create_user(
            email='operator-backoffice@example.com',
            first_name='Óscar',
            password='test-password',
            is_staff=True,
        )
        cls.admin = User.objects.create_superuser(
            email='admin-backoffice@example.com',
            first_name='Admin',
            password='test-password',
        )
        cls.center = RecyclingCenter.objects.create(
            name='Punto Limpio Norte',
            slug='punto-limpio-norte-backoffice',
        )
        UserCenterAccess.objects.create(
            user=cls.operator,
            center=cls.center,
            role=UserCenterAccess.ScopeRole.OPERATOR,
        )
        cls.category = Category.objects.create(
            name='Hogar backoffice',
            slug='hogar-backoffice',
        )
        cls.pending_publication = Publication.objects.create(
            submitter=cls.citizen,
            title='Lámpara pendiente',
            description='Lista para moderación.',
            category=cls.category,
            status=Publication.Status.PENDING_REVIEW,
            submitted_at=timezone.now() - timedelta(hours=8),
        )
        cls.reception_publication = Publication.objects.create(
            submitter=cls.citizen,
            title='Mesa aprobada',
            description='Lista para recepción.',
            category=cls.category,
            status=Publication.Status.APPROVED,
            submitted_at=timezone.now() - timedelta(days=1),
            approved_at=timezone.now() - timedelta(hours=3),
        )
        pickup_publication = Publication.objects.create(
            submitter=cls.citizen,
            title='Silla reservada',
            description='Lista para recoger.',
            category=cls.category,
            status=Publication.Status.APPROVED,
            submitted_at=timezone.now() - timedelta(days=2),
            approved_at=timezone.now() - timedelta(days=1),
        )
        cls.pickup_item = InventoryItem.objects.create(
            reference='BO-0001',
            owner=cls.citizen,
            publication=pickup_publication,
            center=cls.center,
            title=pickup_publication.title,
            description=pickup_publication.description,
            category=cls.category.name,
            category_node=cls.category,
            condition=ReusableObject.Condition.GOOD,
            status=ReusableObject.Status.AVAILABLE,
            received_at=timezone.now() - timedelta(days=1),
            validated_at=timezone.now() - timedelta(days=1),
            validated_by=cls.operator,
        )
        cls.reservation = Reservation.objects.create(
            inventory_item=cls.pickup_item,
            user=cls.admin,
            expires_at=timezone.now() + timedelta(days=1),
        )

    def test_dashboard_requires_login_and_rejects_citizens(self):
        url = reverse('backoffice:dashboard')
        response = self.client.get(url)
        self.assertRedirects(response, f'{reverse("login")}?next={url}')

        self.client.force_login(self.citizen)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_basic_staff_can_open_dashboard_without_privileged_modules(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse('backoffice:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Buenos días, Staff')
        self.assertNotContains(response, 'Buscar usuario')
        self.assertNotContains(response, 'Nueva recepción')

    def test_moderator_sees_moderation_work_only(self):
        self.client.force_login(self.moderator)

        response = self.client.get(reverse('backoffice:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Por moderar')
        self.assertContains(response, self.pending_publication.title)
        self.assertNotContains(response, self.reception_publication.title)
        self.assertContains(response, reverse('moderation:queue'))

    def test_operator_sees_reception_pickups_and_assigned_center(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse('backoffice:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pendientes de recepción')
        self.assertContains(response, self.reception_publication.title)
        self.assertContains(response, self.pickup_item.title)
        self.assertContains(response, self.center.name)
        self.assertContains(response, reverse('backoffice:pickup_queue'))

    def test_user_and_operator_management_are_admin_only(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse('backoffice:user_list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('backoffice:team_list')).status_code, 403)

        self.client.force_login(self.admin)
        users_response = self.client.get(reverse('backoffice:user_list'))
        team_response = self.client.get(reverse('backoffice:team_list'))

        self.assertEqual(users_response.status_code, 200)
        self.assertContains(users_response, self.citizen.email)
        self.assertNotContains(users_response, self.operator.email)
        self.assertEqual(team_response.status_code, 200)
        self.assertContains(team_response, self.operator.email)

    def test_pickup_queue_links_to_existing_qr_flow(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse('backoffice:pickup_queue'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.pickup_item.title)
        self.assertContains(
            response,
            reverse('reservations:pickup_scan', args=[self.reservation.qr_token()]),
        )
