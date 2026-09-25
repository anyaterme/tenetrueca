from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from catalog.models import Category, ReusableObject
from inventory.models import InventoryItem
from locations.models import RecyclingCenter
from publications.models import Publication
from django.utils import timezone


class HealthTests(TestCase):
    def test_health_returns_database_status(self):
        response = self.client.get(reverse('health'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['checks']['database'], 'ok')

    def test_home_renders(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Dale una segunda vida a lo que ya no usas')
        self.assertEqual(response.context['publish_url'], reverse('register'))
        self.assertEqual(response.context['start_url'], reverse('register'))
        self.assertContains(response, f'href="{reverse("catalog:list")}"')
        self.assertContains(response, f'href="{reverse("legal_notice")}"')
        self.assertContains(response, f'href="{reverse("cookie_policy")}"')
        self.assertContains(response, f'href="{reverse("privacy_policy")}"')
        self.assertContains(response, '/static/img/home/step-publish.png')
        self.assertContains(response, '/static/img/home/step-find.png')
        self.assertContains(response, '/static/img/home/step-exchange.png')
        self.assertContains(response, 'alt="Persona publicando la fotografía de un objeto desde su teléfono"')

    def test_legal_pages_render_with_internal_navigation(self):
        pages = (
            ('legal_notice', 'Titularidad y alcance'),
            ('cookie_policy', 'Cookies utilizadas por TRUEC@'),
            ('privacy_policy', 'Responsable y encargado del tratamiento'),
        )

        for url_name, expected_text in pages:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected_text)
                self.assertContains(response, reverse('legal_notice'))
                self.assertContains(response, reverse('cookie_policy'))
                self.assertContains(response, reverse('privacy_policy'))

    def test_cookie_policy_describes_current_technical_cookies(self):
        response = self.client.get(reverse('cookie_policy'))

        self.assertContains(response, 'csrftoken')
        self.assertContains(response, 'sessionid')
        self.assertNotContains(response, 'Ayuntamiento de Madrid')


class HomeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            email='owner@example.com',
            username='owner',
            first_name='Owner',
            password='test-password',
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.center = RecyclingCenter.objects.create(
            name='Punto limpio de prueba',
            slug='punto-limpio-prueba',
        )
        cls.category = Category.objects.create(name='Hogar', slug='hogar')
        for index in range(5):
            publication = Publication.objects.create(
                submitter=cls.owner,
                title=f'Objeto disponible {index}',
                description=f'Descripción real del objeto {index}',
                category=cls.category,
                status=Publication.Status.APPROVED,
                approved_at=timezone.now(),
            )
            InventoryItem.objects.create(
                reference=f'AVAILABLE-{index}',
                owner=cls.owner,
                publication=publication,
                center=cls.center,
                title=f'Objeto disponible {index}',
                description=f'Descripción real del objeto {index}',
                category='Hogar',
                category_node=cls.category,
                condition=ReusableObject.Condition.GOOD,
                status=ReusableObject.Status.AVAILABLE,
                validated_at=timezone.now(),
                validated_by=cls.owner,
            )
        pending_publication = Publication.objects.create(
            submitter=cls.owner,
            title='Objeto pendiente oculto',
            description='No debe mostrarse.',
            category=cls.category,
            status=Publication.Status.APPROVED,
        )
        InventoryItem.objects.create(
            reference='PENDING-1',
            owner=cls.owner,
            publication=pending_publication,
            center=cls.center,
            title='Objeto pendiente oculto',
            category='Hogar',
            category_node=cls.category,
            condition=ReusableObject.Condition.GOOD,
            status=ReusableObject.Status.AVAILABLE,
            validated_at=timezone.now(),
            validated_by=cls.owner,
        )
        pending_publication.status = Publication.Status.PENDING_REVIEW
        pending_publication.save()

    def test_home_shows_only_four_available_objects(self):
        response = self.client.get(reverse('home'))

        featured_objects = list(response.context['featured_objects'])
        self.assertEqual(len(featured_objects), 4)
        self.assertTrue(all(item.status == ReusableObject.Status.AVAILABLE for item in featured_objects))
        self.assertNotContains(response, 'Objeto pendiente oculto')
        self.assertContains(response, 'Punto limpio de prueba', count=4)

    def test_authenticated_publish_action_uses_citizen_flow(self):
        staff = User.objects.create_user(
            email='staff@example.com',
            username='staff',
            first_name='Staff',
            password='test-password',
            account_status=User.AccountStatus.ACTIVE,
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.get(reverse('home'))

        self.assertEqual(response.context['publish_url'], reverse('publications:create'))

    def test_empty_catalog_shows_real_empty_state(self):
        ReusableObject.objects.all().delete()

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'Aún no hay objetos disponibles')
