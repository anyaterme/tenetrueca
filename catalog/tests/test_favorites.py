import re

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, Favorite, ReusableObject
from inventory.models import InventoryItem
from locations.models import RecyclingCenter
from publications.models import Publication


class FavoriteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(email='favorite-owner@example.com', first_name='Owner')
        cls.user = User.objects.create_user(email='favorite-user@example.com', first_name='User')
        cls.other_user = User.objects.create_user(email='favorite-other@example.com', first_name='Other')
        cls.operator = User.objects.create_user(email='favorite-operator@example.com', first_name='Operator')
        cls.center = RecyclingCenter.objects.create(name='Centro favoritos', slug='centro-favoritos')
        cls.category = Category.objects.create(name='Favoritos', slug='favoritos-test')
        cls.publication = Publication.objects.create(
            submitter=cls.owner,
            title='Objeto favorito',
            description='Objeto disponible para guardar.',
            category=cls.category,
            status=Publication.Status.APPROVED,
            approved_at=timezone.now(),
        )
        cls.item = InventoryItem.objects.create(
            reference='FAV-0001',
            owner=cls.owner,
            publication=cls.publication,
            center=cls.center,
            title=cls.publication.title,
            description=cls.publication.description,
            category=cls.category.name,
            category_node=cls.category,
            condition=ReusableObject.Condition.GOOD,
            status=ReusableObject.Status.AVAILABLE,
            received_at=timezone.now(),
            validated_at=timezone.now(),
            validated_by=cls.operator,
        )

    def test_add_is_post_only_and_idempotent(self):
        self.client.force_login(self.user)
        url = reverse('catalog:favorite_add', args=[self.item.reference])

        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        self.client.post(url)

        self.assertEqual(
            Favorite.objects.filter(user=self.user, reusable_object=self.item).count(),
            1,
        )

    def test_remove_only_affects_authenticated_user(self):
        Favorite.objects.create(user=self.other_user, reusable_object=self.item)
        self.client.force_login(self.user)

        self.client.post(reverse('catalog:favorite_remove', args=[self.item.reference]))

        self.assertTrue(
            Favorite.objects.filter(user=self.other_user, reusable_object=self.item).exists()
        )

    def test_favorite_list_is_isolated_and_marks_unavailable_objects(self):
        Favorite.objects.create(user=self.user, reusable_object=self.item)
        Favorite.objects.create(user=self.other_user, reusable_object=self.item)
        InventoryItem.objects.filter(pk=self.item.pk).update(status=ReusableObject.Status.RESERVED)
        self.client.force_login(self.user)

        response = self.client.get(reverse('catalog:favorites'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['favorite_cards']), 1)
        self.assertContains(response, 'No disponible')
        self.assertNotContains(response, self.item.get_absolute_url())

    def test_catalog_card_and_detail_offer_favorite_post_actions(self):
        self.client.force_login(self.user)

        list_response = self.client.get(reverse('catalog:list'))
        detail_response = self.client.get(self.item.get_absolute_url())

        add_url = reverse('catalog:favorite_add', args=[self.item.reference])
        self.assertContains(list_response, add_url)
        self.assertContains(
            list_response,
            f'aria-label="Añadir {self.item.title} a favoritos"',
        )
        self.assertContains(detail_response, add_url)

        Favorite.objects.create(user=self.user, reusable_object=self.item)
        detail_response = self.client.get(self.item.get_absolute_url())
        self.assertContains(
            detail_response,
            reverse('catalog:favorite_remove', args=[self.item.reference]),
        )

    def test_catalog_card_csrf_tokens_allow_adding_and_removing_favorites(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        list_response = csrf_client.get(reverse('catalog:list'))
        add_form = re.search(
            r'<form class="favorite-action"[^>]*>.*?'
            r'name="csrfmiddlewaretoken" value="([^"]+)".*?</form>',
            list_response.content.decode(),
            re.DOTALL,
        )

        self.assertIsNotNone(add_form)
        add_response = csrf_client.post(
            reverse('catalog:favorite_add', args=[self.item.reference]),
            {
                'csrfmiddlewaretoken': add_form.group(1),
                'next': reverse('catalog:list'),
            },
        )
        self.assertRedirects(add_response, reverse('catalog:list'))
        self.assertTrue(
            Favorite.objects.filter(user=self.user, reusable_object=self.item).exists()
        )

        favorites_response = csrf_client.get(reverse('catalog:favorites'))
        remove_form = re.search(
            r'<form class="favorite-action"[^>]*>.*?'
            r'name="csrfmiddlewaretoken" value="([^"]+)".*?</form>',
            favorites_response.content.decode(),
            re.DOTALL,
        )

        self.assertIsNotNone(remove_form)
        remove_response = csrf_client.post(
            reverse('catalog:favorite_remove', args=[self.item.reference]),
            {
                'csrfmiddlewaretoken': remove_form.group(1),
                'next': reverse('catalog:favorites'),
            },
        )
        self.assertRedirects(remove_response, reverse('catalog:favorites'))
        self.assertFalse(
            Favorite.objects.filter(user=self.user, reusable_object=self.item).exists()
        )
