from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from catalog.models import Category
from inventory.models import InventoryItem
from locations.models import RecyclingCenter
from publications.models import Publication


class InventoryItemModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            email='owner@example.com',
            first_name='Owner',
            password='test-password',
        )
        cls.validator = User.objects.create_user(
            email='validator@example.com',
            first_name='Validator',
            password='test-password',
        )
        cls.category = Category.objects.create(name='Hogar', slug='hogar')
        cls.center = RecyclingCenter.objects.create(name='Punto limpio', slug='punto-limpio')

    def create_item(self, reference, status):
        publication = Publication.objects.create(
            submitter=self.user,
            title=reference,
            description='Objeto para inventario.',
            category=self.category,
            status=Publication.Status.APPROVED,
        )
        return InventoryItem.objects.create(
            reference=reference,
            owner=self.user,
            publication=publication,
            center=self.center,
            title=publication.title,
            description=publication.description,
            category=self.category.name,
            category_node=self.category,
            internal_location='A-01',
            condition=InventoryItem.Condition.GOOD,
            status=status,
            validated_at=timezone.now() if status != InventoryItem.Status.PENDING_VALIDATION else None,
            validated_by=self.validator if status != InventoryItem.Status.PENDING_VALIDATION else None,
        )

    def test_available_queryset_exposes_only_available_items(self):
        available = self.create_item('INV-AVAILABLE', InventoryItem.Status.AVAILABLE)
        self.create_item('INV-RESERVED', InventoryItem.Status.RESERVED)

        self.assertEqual(list(InventoryItem.objects.available()), [available])

    def test_inventory_relations_are_protected(self):
        item = self.create_item('INV-PROTECTED', InventoryItem.Status.AVAILABLE)

        with self.assertRaises(ProtectedError):
            item.center.delete()
        with self.assertRaises(ProtectedError):
            item.publication.delete()

    def test_inventory_requires_an_approved_publication(self):
        publication = Publication.objects.create(
            submitter=self.user,
            title='Pendiente',
            description='Objeto todavía pendiente de moderación.',
            category=self.category,
            status=Publication.Status.PENDING_REVIEW,
        )

        with self.assertRaises(ValidationError):
            InventoryItem.objects.create(
                reference='INV-PENDING',
                owner=self.user,
                publication=publication,
                center=self.center,
                title=publication.title,
                description=publication.description,
                category=self.category.name,
                category_node=self.category,
                internal_location='A-02',
                condition=InventoryItem.Condition.GOOD,
            )
