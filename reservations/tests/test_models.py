from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from catalog.models import Category
from inventory.models import InventoryItem
from locations.models import RecyclingCenter
from publications.models import Publication
from reservations.models import Reservation


class ReservationModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(
            email='owner@example.com',
            first_name='Owner',
            password='test-password',
        )
        cls.requester = User.objects.create_user(
            email='requester@example.com',
            first_name='Requester',
            password='test-password',
        )
        cls.other_requester = User.objects.create_user(
            email='other@example.com',
            first_name='Other',
            password='test-password',
        )
        cls.validator = User.objects.create_user(
            email='validator@example.com',
            first_name='Validator',
            password='test-password',
        )
        category = Category.objects.create(name='Hogar', slug='hogar')
        center = RecyclingCenter.objects.create(name='Punto limpio', slug='punto-limpio')
        publication = Publication.objects.create(
            submitter=cls.owner,
            title='Silla',
            description='Silla en buen estado.',
            category=category,
            status=Publication.Status.APPROVED,
        )
        cls.item = InventoryItem.objects.create(
            reference='INV-0001',
            owner=cls.owner,
            publication=publication,
            center=center,
            title=publication.title,
            description=publication.description,
            category=category.name,
            category_node=category,
            internal_location='A-01',
            condition=InventoryItem.Condition.GOOD,
            status=InventoryItem.Status.AVAILABLE,
            validated_at=timezone.now(),
            validated_by=cls.validator,
        )

    def reservation_data(self, user):
        return {
            'inventory_item': self.item,
            'user': user,
            'reserved_at': timezone.now(),
            'expires_at': timezone.now() + timedelta(days=7),
        }

    def test_database_allows_only_one_active_reservation_per_item(self):
        Reservation.objects.create(**self.reservation_data(self.requester))
        duplicate = Reservation(**self.reservation_data(self.other_requester))

        with self.assertRaises(IntegrityError), transaction.atomic():
            Reservation.objects.bulk_create([duplicate])

    def test_reservation_does_not_change_inventory_status(self):
        Reservation.objects.create(**self.reservation_data(self.requester))

        self.item.refresh_from_db()
        self.assertEqual(self.item.status, InventoryItem.Status.AVAILABLE)
        self.assertNotIn(self.item, InventoryItem.objects.available())

    def test_terminal_inventory_item_cannot_receive_active_reservation(self):
        for status in (InventoryItem.Status.DELIVERED, InventoryItem.Status.WITHDRAWN):
            with self.subTest(status=status):
                self.item.status = status
                self.item.save(update_fields=['status'])

                with self.assertRaises(ValidationError):
                    Reservation.objects.create(**self.reservation_data(self.requester))
