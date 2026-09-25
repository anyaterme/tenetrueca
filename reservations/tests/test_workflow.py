from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCenterAccess
from audit.models import AuditEvent
from catalog.models import Category, ReusableObject
from inventory.models import InventoryItem
from locations.models import RecyclingCenter
from operations.models import Operation
from points.models import PointMovement
from points.services import InsufficientPoints, award_registration_points, points_balance
from publications.models import Publication
from reservations.models import Reservation
from reservations.services import (
    InvalidReservationToken,
    cancel_reservation,
    complete_pickup,
    create_reservation,
    expire_due_reservations,
    reservation_from_qr_token,
)


class ReservationWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(
            email='reservation-owner@example.com',
            username='reservation-owner',
            first_name='Owner',
            password='test-password',
        )
        cls.requester = User.objects.create_user(
            email='reservation-requester@example.com',
            username='reservation-requester',
            first_name='Requester',
            password='test-password',
        )
        cls.other_user = User.objects.create_user(
            email='reservation-other@example.com',
            username='reservation-other',
            first_name='Other',
            password='test-password',
        )
        cls.operator = User.objects.create_user(
            email='reservation-operator@example.com',
            username='reservation-operator',
            first_name='Operator',
            password='test-password',
        )
        cls.other_operator = User.objects.create_user(
            email='reservation-other-operator@example.com',
            username='reservation-other-operator',
            first_name='Other operator',
            password='test-password',
        )
        cls.validator = User.objects.create_user(
            email='reservation-validator@example.com',
            username='reservation-validator',
            first_name='Validator',
            password='test-password',
        )
        cls.center = RecyclingCenter.objects.create(name='Centro Norte', slug='centro-norte')
        cls.other_center = RecyclingCenter.objects.create(name='Centro Sur', slug='centro-sur')
        UserCenterAccess.objects.create(
            user=cls.operator,
            center=cls.center,
            role=UserCenterAccess.ScopeRole.OPERATOR,
        )
        UserCenterAccess.objects.create(
            user=cls.other_operator,
            center=cls.other_center,
            role=UserCenterAccess.ScopeRole.OPERATOR,
        )
        cls.category = Category.objects.create(name='Muebles', slug='muebles')
        award_registration_points(user=cls.requester)

    def create_item(self, title='Mesa reservable', *, center=None, status=ReusableObject.Status.AVAILABLE):
        center = center or self.center
        publication = Publication.objects.create(
            submitter=self.owner,
            title=title,
            description='Mesa de madera en buen estado.',
            category=self.category,
            status=Publication.Status.APPROVED,
            approved_at=timezone.now(),
        )
        return InventoryItem.objects.create(
            reference=f'RSV-{InventoryItem.objects.count() + 1:04d}',
            owner=self.owner,
            publication=publication,
            center=center,
            title=title,
            description=publication.description,
            category=self.category.name,
            category_node=self.category,
            condition=ReusableObject.Condition.GOOD,
            status=status,
            received_at=timezone.now(),
            validated_at=timezone.now(),
            validated_by=self.validator,
        )

    @override_settings(RESERVATION_EXPIRATION_HOURS=24)
    def test_create_reservation_uses_session_user_and_removes_item_from_catalog(self):
        item = self.create_item()

        reservation = create_reservation(inventory_item_id=item.pk, user=self.requester)

        self.assertEqual(reservation.user, self.requester)
        self.assertEqual(reservation.status, Reservation.Status.ACTIVE)
        self.assertGreater(reservation.expires_at, reservation.reserved_at + timedelta(hours=23))
        item.refresh_from_db()
        self.assertEqual(item.status, InventoryItem.Status.RESERVED)
        self.assertFalse(InventoryItem.objects.public_catalog().filter(pk=item.pk).exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                action='reservations.created',
                entity_id=str(reservation.public_id),
            ).exists()
        )

    def test_create_reservation_rejects_unavailable_or_simultaneous_reservations(self):
        item = self.create_item()
        create_reservation(inventory_item_id=item.pk, user=self.requester)

        with self.assertRaisesMessage(ValidationError, 'disponible'):
            create_reservation(inventory_item_id=item.pk, user=self.other_user)

        self.assertEqual(
            Reservation.objects.filter(inventory_item=item, status=Reservation.Status.ACTIVE).count(),
            1,
        )

    def test_create_reservation_requires_enough_points(self):
        item = self.create_item()

        with self.assertRaises(InsufficientPoints):
            create_reservation(inventory_item_id=item.pk, user=self.other_user)

        self.assertEqual(item.status, InventoryItem.Status.AVAILABLE)
        self.assertFalse(Reservation.objects.filter(inventory_item=item).exists())

    def test_owner_cannot_reserve_own_published_item(self):
        item = self.create_item('Objeto propio')

        with self.assertRaisesMessage(
            ValidationError,
            'No puedes reservar un objeto que has publicado.',
        ):
            create_reservation(inventory_item_id=item.pk, user=self.owner)

        item.refresh_from_db()
        self.assertEqual(item.status, InventoryItem.Status.AVAILABLE)
        self.assertFalse(Reservation.objects.filter(inventory_item=item).exists())

    def test_owner_detail_hides_reservation_action_and_post_is_rejected(self):
        item = self.create_item('Objeto propio visible')
        detail_url = reverse('catalog:detail', args=[item.reference])
        create_url = reverse('reservations:create', args=[item.reference])
        self.client.force_login(self.owner)

        response = self.client.get(detail_url)

        self.assertContains(response, 'No puedes reservar un objeto que has publicado.')
        self.assertNotContains(response, '>Reservar objeto</button>')

        response = self.client.post(create_url, follow=True)

        self.assertRedirects(response, detail_url)
        self.assertContains(response, 'No puedes reservar un objeto que has publicado.')
        self.assertFalse(Reservation.objects.filter(inventory_item=item).exists())

    def test_cancel_active_reservation_invalidates_qr_and_releases_offerable_item(self):
        item = self.create_item()
        reservation = create_reservation(inventory_item_id=item.pk, user=self.requester)
        token = reservation.qr_token()

        reservation, released = cancel_reservation(
            reservation_public_id=reservation.public_id,
            user=self.requester,
        )

        self.assertTrue(released)
        reservation.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)
        self.assertIsNotNone(reservation.qr_invalidated_at)
        self.assertEqual(item.status, InventoryItem.Status.AVAILABLE)
        self.assertEqual(points_balance(self.requester), 100)
        self.assertTrue(InventoryItem.objects.public_catalog().filter(pk=item.pk).exists())
        with self.assertRaises(InvalidReservationToken):
            reservation_from_qr_token(token)

    def test_cancel_does_not_return_item_when_public_rules_no_longer_match(self):
        item = self.create_item()
        reservation = create_reservation(inventory_item_id=item.pk, user=self.requester)
        self.category.is_active = False
        self.category.save()

        reservation, released = cancel_reservation(
            reservation_public_id=reservation.public_id,
            user=self.requester,
        )

        item.refresh_from_db()
        self.assertFalse(released)
        self.assertEqual(item.status, InventoryItem.Status.RESERVED)

    def test_user_can_only_see_own_reservations(self):
        item = self.create_item()
        reservation = create_reservation(inventory_item_id=item.pk, user=self.requester)
        self.client.force_login(self.other_user)

        response = self.client.get(reverse('reservations:detail', args=[reservation.public_id]))

        self.assertEqual(response.status_code, 404)

    def test_expiration_is_idempotent_and_releases_offerable_item(self):
        item = self.create_item()
        reservation = create_reservation(inventory_item_id=item.pk, user=self.requester)
        now = timezone.now()
        Reservation.objects.filter(pk=reservation.pk).update(
            reserved_at=now - timedelta(minutes=2),
            expires_at=now - timedelta(minutes=1),
        )

        first_count = expire_due_reservations()
        second_count = expire_due_reservations()

        reservation.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(reservation.status, Reservation.Status.EXPIRED)
        self.assertIsNotNone(reservation.qr_invalidated_at)
        self.assertEqual(item.status, InventoryItem.Status.AVAILABLE)
        self.assertEqual(points_balance(self.requester), 100)
        self.assertEqual(
            AuditEvent.objects.filter(action='reservations.expired', entity_id=str(reservation.public_id)).count(),
            1,
        )

    def test_expiration_command_reports_expired_count(self):
        reservation = create_reservation(inventory_item_id=self.create_item().pk, user=self.requester)
        now = timezone.now()
        Reservation.objects.filter(pk=reservation.pk).update(
            reserved_at=now - timedelta(minutes=2),
            expires_at=now - timedelta(minutes=1),
        )
        output = StringIO()

        call_command('expire_reservations', stdout=output)

        self.assertIn('Reservas expiradas: 1', output.getvalue())

    def test_qr_pickup_requires_center_permission_and_get_does_not_complete(self):
        item = self.create_item()
        reservation = create_reservation(inventory_item_id=item.pk, user=self.requester)
        token = reservation.qr_token()

        with self.assertRaises(PermissionDenied):
            complete_pickup(token=token, operator=self.other_operator)

        self.client.force_login(self.operator)
        response = self.client.get(reverse('reservations:pickup_scan', args=[token]))

        self.assertEqual(response.status_code, 200)
        reservation.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.ACTIVE)
        self.assertEqual(item.status, InventoryItem.Status.RESERVED)
        self.assertTrue(
            AuditEvent.objects.filter(
                action='reservations.pickup.valid_attempt',
                entity_id=str(reservation.public_id),
            ).exists()
        )

    def test_complete_pickup_is_transactional_and_rejects_reuse(self):
        item = self.create_item()
        reservation = create_reservation(inventory_item_id=item.pk, user=self.requester)
        token = reservation.qr_token()

        completed_reservation, completed = complete_pickup(token=token, operator=self.operator)

        self.assertTrue(completed)
        completed_reservation.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(completed_reservation.status, Reservation.Status.COMPLETED)
        self.assertEqual(completed_reservation.pickup_operator, self.operator)
        self.assertEqual(completed_reservation.pickup_center, self.center)
        self.assertEqual(item.status, InventoryItem.Status.DELIVERED)
        self.assertIsNotNone(completed_reservation.qr_invalidated_at)
        self.assertEqual(points_balance(self.requester), 50)
        self.assertEqual(
            PointMovement.objects.filter(
                user=self.requester,
                reason=PointMovement.Reason.OBJECT_PICKED_UP,
                reference=f'reservation-pickup:{reservation.pk}',
            ).count(),
            1,
        )
        self.assertIsNotNone(
            PointMovement.objects.get(reference=f'reservation-pickup:{reservation.pk}').operation_id
        )

        with self.assertRaises(InvalidReservationToken):
            complete_pickup(token=token, operator=self.operator)
        self.assertEqual(
            Operation.objects.filter(
                operation_type=Operation.Type.PICKUP,
                reusable_object=item,
                status=Operation.Status.COMPLETED,
            ).count(),
            1,
        )

    def test_pickup_with_insufficient_balance_keeps_reservation_active(self):
        first = create_reservation(
            inventory_item_id=self.create_item('Primera recogida').pk,
            user=self.requester,
        )
        second = create_reservation(
            inventory_item_id=self.create_item('Segunda recogida').pk,
            user=self.requester,
        )
        third = create_reservation(
            inventory_item_id=self.create_item('Sin saldo').pk,
            user=self.requester,
        )
        complete_pickup(token=first.qr_token(), operator=self.operator)
        complete_pickup(token=second.qr_token(), operator=self.operator)

        with self.assertRaises(InsufficientPoints):
            complete_pickup(token=third.qr_token(), operator=self.operator)

        third.refresh_from_db()
        third.inventory_item.refresh_from_db()
        self.assertEqual(third.status, Reservation.Status.ACTIVE)
        self.assertEqual(third.inventory_item.status, InventoryItem.Status.RESERVED)
        self.assertIsNone(third.qr_invalidated_at)
        self.assertEqual(points_balance(self.requester), 0)
        self.assertFalse(
            PointMovement.objects.filter(reference=f'reservation-pickup:{third.pk}').exists()
        )

    def test_invalid_cancelled_expired_and_completed_qr_are_rejected(self):
        cancelled = create_reservation(inventory_item_id=self.create_item('Cancelada').pk, user=self.requester)
        cancelled_token = cancelled.qr_token()
        cancel_reservation(reservation_public_id=cancelled.public_id, user=self.requester)

        expired = create_reservation(inventory_item_id=self.create_item('Caducada').pk, user=self.requester)
        expired_token = expired.qr_token()
        now = timezone.now()
        Reservation.objects.filter(pk=expired.pk).update(
            reserved_at=now - timedelta(minutes=2),
            expires_at=now - timedelta(minutes=1),
        )

        completed = create_reservation(inventory_item_id=self.create_item('Completada').pk, user=self.requester)
        completed_token = completed.qr_token()
        complete_pickup(token=completed_token, operator=self.operator)

        for token in ('no-es-un-token', cancelled_token, expired_token, completed_token):
            with self.subTest(token=token[:12]):
                with self.assertRaises(InvalidReservationToken):
                    reservation_from_qr_token(token)
