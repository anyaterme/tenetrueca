from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, ReusableObject
from inventory.models import InventoryItem
from locations.models import RecyclingCenter
from points.services import award_registration_points
from publications.models import Publication
from reservations.models import Reservation


class CitizenDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            email='dashboard@example.com',
            username='dashboard-user',
            first_name='Lucía con un nombre largo',
            password='dashboard-password',
        )
        cls.other_user = User.objects.create_user(
            email='other-dashboard@example.com',
            first_name='Otra',
            password='dashboard-password',
        )
        cls.staff_user = User.objects.create_user(
            email='dashboard-staff@example.com',
            first_name='Staff',
            password='dashboard-password',
            is_staff=True,
        )
        award_registration_points(user=cls.user)
        award_registration_points(user=cls.other_user)
        cls.center = RecyclingCenter.objects.create(
            name='Punto Limpio Dashboard',
            slug='punto-dashboard',
        )
        cls.category = Category.objects.create(name='Hogar dashboard', slug='hogar-dashboard')
        cls.own_publication = Publication.objects.create(
            submitter=cls.user,
            title='Lámpara propia en revisión',
            description='Publicación visible solo en la cuenta propia.',
            category=cls.category,
            status=Publication.Status.PENDING_REVIEW,
            submitted_at=timezone.now(),
        )
        Publication.objects.create(
            submitter=cls.other_user,
            title='Publicación de otra persona',
            description='No debe aparecer en el dashboard.',
            category=cls.category,
            status=Publication.Status.DRAFT,
        )
        own_item = cls._create_item('Reserva propia', 'DASH-OWN', cls.other_user)
        other_item = cls._create_item('Reserva ajena', 'DASH-OTHER', cls.staff_user)
        cls.own_reservation = Reservation.objects.create(
            inventory_item=own_item,
            user=cls.user,
            expires_at=timezone.now() + timedelta(days=1),
        )
        Reservation.objects.create(
            inventory_item=other_item,
            user=cls.other_user,
            expires_at=timezone.now() + timedelta(days=1),
        )

    @classmethod
    def _create_item(cls, title, reference, owner):
        publication = Publication.objects.create(
            submitter=owner,
            title=title,
            description='Objeto para una reserva del dashboard.',
            category=cls.category,
            status=Publication.Status.APPROVED,
            approved_at=timezone.now(),
        )
        return InventoryItem.objects.create(
            reference=reference,
            owner=owner,
            publication=publication,
            center=cls.center,
            title=title,
            description=publication.description,
            category=cls.category.name,
            category_node=cls.category,
            condition=ReusableObject.Condition.GOOD,
            status=ReusableObject.Status.AVAILABLE,
            received_at=timezone.now(),
            validated_at=timezone.now(),
            validated_by=cls.staff_user,
        )

    def test_dashboard_requires_authentication(self):
        response = self.client.get(reverse('dashboard'))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('dashboard')}")

    def test_dashboard_uses_real_own_account_data(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['points_balance'], 100)
        self.assertEqual(response.context['publication_count'], 1)
        self.assertEqual(response.context['active_reservation_count'], 1)
        self.assertContains(response, self.user.first_name)
        self.assertContains(response, self.own_publication.title)
        self.assertContains(response, self.own_reservation.inventory_item.title)
        self.assertNotContains(response, 'Publicación de otra persona')
        self.assertNotContains(response, 'Reserva ajena')

    def test_dashboard_has_working_citizen_navigation(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        for label in ('Catálogo', 'Publicar', 'Actividad', 'Cuenta'):
            self.assertContains(response, label)
        self.assertContains(response, reverse('catalog:list'))
        self.assertContains(response, reverse('publications:create'))
        self.assertContains(response, reverse('catalog:favorites'))
        self.assertContains(response, 'citizen-mobile-nav')
        self.assertContains(
            response,
            f'href="{reverse("dashboard")}#actividad" class="is-active" '
            'aria-current="page"',
        )

    def test_dashboard_links_to_existing_review_reason_for_attention_status(self):
        self.own_publication.status = Publication.Status.REJECTED
        self.own_publication.save(update_fields=['status', 'updated_at'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'Consultar motivo')
        self.assertContains(
            response,
            reverse('publications:detail', args=[self.own_publication.pk]),
        )

    def test_dashboard_does_not_invent_review_action_for_pending_publication(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertNotContains(response, 'Consultar motivo')

    def test_staff_dashboard_redirects_to_existing_profile_and_has_no_citizen_nav(self):
        self.client.force_login(self.staff_user)

        dashboard_response = self.client.get(reverse('dashboard'))
        profile_response = self.client.get(reverse('profile'))

        self.assertRedirects(dashboard_response, reverse('profile'))
        self.assertNotContains(profile_response, 'citizen-mobile-nav')

    def test_empty_dashboard_offers_catalog(self):
        empty_user = get_user_model().objects.create_user(
            email='empty-dashboard@example.com',
            first_name='Sin actividad',
        )
        self.client.force_login(empty_user)

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'Tu actividad empezará aquí')
        self.assertContains(response, reverse('catalog:list'))
