from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from points.models import PointMovement
from points.services import (
    InsufficientPoints,
    award_registration_points,
    create_point_movement,
    points_balance,
)


class PointLedgerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='points-user@example.com',
            first_name='Points',
        )

    def test_registration_credit_is_idempotent_and_not_retroactive(self):
        self.assertEqual(points_balance(self.user), 0)

        first, first_created = award_registration_points(user=self.user)
        second, second_created = award_registration_points(user=self.user)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first, second)
        self.assertEqual(points_balance(self.user), 100)

    def test_ledger_is_append_only(self):
        movement, _ = award_registration_points(user=self.user)
        movement.amount = 200

        with self.assertRaises(ValidationError):
            movement.save()
        with self.assertRaises(ValidationError):
            movement.delete()
        with self.assertRaises(ValidationError):
            PointMovement.objects.filter(pk=movement.pk).update(amount=200)
        with self.assertRaises(ValidationError):
            PointMovement.objects.filter(pk=movement.pk).delete()

    def test_movements_page_only_shows_authenticated_users_ledger(self):
        other = get_user_model().objects.create_user(email='points-other@example.com', first_name='Other')
        award_registration_points(user=self.user)
        award_registration_points(user=other)
        self.client.force_login(self.user)

        response = self.client.get(reverse('points:movements'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['balance'], 100)
        self.assertEqual(list(response.context['page_obj'].object_list), list(self.user.point_movements.all()))
        self.assertNotContains(response, other.email)


class ConcurrentPointChargeTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='points-concurrent@example.com',
            first_name='Concurrent',
        )
        PointMovement.objects.create(
            user=self.user,
            amount=100,
            reason=PointMovement.Reason.REGISTRATION,
            reference='concurrency-opening-balance',
        )
        PointMovement.objects.create(
            user=self.user,
            amount=-50,
            reason=PointMovement.Reason.OBJECT_PICKED_UP,
            reference='concurrency-prior-pickup',
        )

    def _charge(self, reference):
        close_old_connections()
        try:
            user = get_user_model().objects.get(pk=self.user.pk)
            create_point_movement(
                user=user,
                amount=-50,
                reason=PointMovement.Reason.OBJECT_PICKED_UP,
                reference=reference,
            )
            return 'charged'
        except InsufficientPoints:
            return 'insufficient'
        finally:
            close_old_connections()

    def test_concurrent_charges_cannot_make_balance_negative(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self._charge, ('charge-a', 'charge-b')))

        self.assertCountEqual(results, ['charged', 'insufficient'])
        self.user.refresh_from_db()
        self.assertEqual(points_balance(self.user), 0)
        self.assertEqual(
            self.user.point_movements.filter(reference__in=['charge-a', 'charge-b']).count(),
            1,
        )
