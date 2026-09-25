from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCenterAccess
from audit.models import AuditEvent
from catalog.models import Category, ReusableObject
from inventory.models import InventoryItem, ReceptionInspection
from inventory.services import inspect_reception
from locations.models import RecyclingCenter
from publications.models import Publication


class ReceptionWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(
            email='reception-owner@example.com',
            username='reception-owner',
            first_name='Owner',
            password='test-password',
        )
        cls.operator = User.objects.create_user(
            email='reception-operator@example.com',
            username='reception-operator',
            first_name='Operator',
            password='test-password',
        )
        cls.outsider = User.objects.create_user(
            email='reception-outsider@example.com',
            username='reception-outsider',
            first_name='Outsider',
            password='test-password',
        )
        cls.other_operator = User.objects.create_user(
            email='reception-other-operator@example.com',
            username='reception-other-operator',
            first_name='Other operator',
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

    def create_publication(self, title='Mesa aprobada'):
        return Publication.objects.create(
            submitter=self.owner,
            title=title,
            description='Mesa de madera en buen estado.',
            category=self.category,
            status=Publication.Status.APPROVED,
            submitted_at=timezone.now(),
            reviewed_at=timezone.now(),
            approved_at=timezone.now(),
        )

    def reception_data(self, **overrides):
        data = {
            'center': str(self.center.pk),
            'decision': ReceptionInspection.Decision.ACCEPT,
            'condition': ReusableObject.Condition.GOOD,
            'internal_location': 'Nave 1 · A-04',
            'notes': 'Objeto comprobado y completo.',
        }
        data.update(overrides)
        return data

    def test_user_without_center_access_cannot_use_reception(self):
        publication = self.create_publication()
        self.client.force_login(self.outsider)

        self.assertEqual(self.client.get(reverse('inventory:reception_queue')).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('inventory:reception_detail', args=[publication.pk])).status_code,
            403,
        )

    def test_client_cannot_choose_a_center_outside_operator_scope(self):
        publication = self.create_publication()
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse('inventory:reception_detail', args=[publication.pk]),
            self.reception_data(center=str(self.other_center.pk)),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('center', response.context['form'].errors)
        self.assertFalse(ReceptionInspection.objects.exists())
        self.assertFalse(InventoryItem.objects.filter(publication=publication).exists())

    def test_rejection_requires_reason_and_preserves_history_without_inventory(self):
        publication = self.create_publication()
        self.client.force_login(self.operator)
        url = reverse('inventory:reception_detail', args=[publication.pk])

        response = self.client.post(
            url,
            self.reception_data(decision=ReceptionInspection.Decision.REJECT, notes=''),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Indica el motivo del rechazo')
        self.assertFalse(ReceptionInspection.objects.exists())

        response = self.client.post(
            url,
            self.reception_data(
                decision=ReceptionInspection.Decision.REJECT,
                notes='La estructura está rota.',
            ),
        )
        self.assertRedirects(response, url)
        inspection = ReceptionInspection.objects.get()
        self.assertEqual(inspection.decision, ReceptionInspection.Decision.REJECT)
        self.assertEqual(inspection.operator, self.operator)
        self.assertEqual(inspection.center, self.center)
        self.assertIsNone(inspection.inventory_item)
        self.assertFalse(InventoryItem.objects.filter(publication=publication).exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                action='inventory.reception.rejected',
                entity_id=str(publication.pk),
            ).exists()
        )

    def test_acceptance_creates_one_public_inventory_item_and_is_idempotent(self):
        publication = self.create_publication()

        first_item, first_inspection, first_created = inspect_reception(
            publication_id=publication.pk,
            operator=self.operator,
            center=self.center,
            decision=ReceptionInspection.Decision.ACCEPT,
            condition=ReusableObject.Condition.GOOD,
            internal_location='Nave 1 · A-04',
            notes='Objeto comprobado.',
        )
        second_item, second_inspection, second_created = inspect_reception(
            publication_id=publication.pk,
            operator=self.operator,
            center=self.center,
            decision=ReceptionInspection.Decision.ACCEPT,
            condition=ReusableObject.Condition.GOOD,
            internal_location='Nave 1 · A-04',
            notes='Reintento.',
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first_item, second_item)
        self.assertEqual(first_inspection, second_inspection)
        self.assertEqual(InventoryItem.objects.filter(publication=publication).count(), 1)
        self.assertEqual(
            ReceptionInspection.objects.filter(
                publication=publication,
                decision=ReceptionInspection.Decision.ACCEPT,
            ).count(),
            1,
        )
        self.assertEqual(first_item.owner, self.owner)
        self.assertEqual(first_item.center, self.center)
        self.assertEqual(first_item.status, ReusableObject.Status.AVAILABLE)
        self.assertEqual(first_item.validated_by, self.operator)
        self.assertEqual(InventoryItem.objects.public_catalog().get(), first_item)
        self.assertTrue(
            AuditEvent.objects.filter(
                action='inventory.reception.accepted',
                entity_id=str(first_item.pk),
            ).exists()
        )

    def test_catalog_publishes_only_after_accepted_physical_inspection(self):
        publication = self.create_publication('Objeto listo para recepción')

        before_reception = self.client.get(reverse('catalog:list'))
        self.assertNotContains(before_reception, publication.title)

        item, inspection, created = inspect_reception(
            publication_id=publication.pk,
            operator=self.operator,
            center=self.center,
            decision=ReceptionInspection.Decision.ACCEPT,
            condition=ReusableObject.Condition.GOOD,
            notes='Inspección física superada.',
        )

        self.assertTrue(created)
        self.assertEqual(inspection.inventory_item, item)
        after_reception = self.client.get(reverse('catalog:list'))
        self.assertContains(after_reception, publication.title)
        self.assertEqual(self.client.get(item.get_absolute_url()).status_code, 200)

    def test_acceptance_requires_complete_public_data(self):
        publication = self.create_publication()
        Publication.objects.filter(pk=publication.pk).update(description='')

        with self.assertRaisesMessage(
            ValidationError,
            'La publicación necesita una descripción pública.',
        ):
            inspect_reception(
                publication_id=publication.pk,
                operator=self.operator,
                center=self.center,
                decision=ReceptionInspection.Decision.ACCEPT,
                condition=ReusableObject.Condition.GOOD,
            )

        self.assertFalse(InventoryItem.objects.filter(publication=publication).exists())
        self.assertFalse(ReceptionInspection.objects.filter(publication=publication).exists())

    def test_acceptance_requires_active_category_and_operational_center(self):
        inactive_category_publication = self.create_publication('Categoría inactiva')
        self.category.is_active = False
        self.category.save()

        with self.assertRaisesMessage(ValidationError, 'La categoría debe estar activa'):
            inspect_reception(
                publication_id=inactive_category_publication.pk,
                operator=self.operator,
                center=self.center,
                decision=ReceptionInspection.Decision.ACCEPT,
                condition=ReusableObject.Condition.GOOD,
            )

        self.category.is_active = True
        self.category.save()
        closed_center_publication = self.create_publication('Centro cerrado')
        self.center.operational_status = RecyclingCenter.OperationalStatus.TEMPORARILY_CLOSED
        self.center.save()

        with self.assertRaisesMessage(ValidationError, 'debe estar operativo'):
            inspect_reception(
                publication_id=closed_center_publication.pk,
                operator=self.operator,
                center=self.center,
                decision=ReceptionInspection.Decision.ACCEPT,
                condition=ReusableObject.Condition.GOOD,
            )

        self.assertFalse(InventoryItem.objects.exists())
        self.assertFalse(ReceptionInspection.objects.exists())

    def test_reception_view_explains_why_object_cannot_be_published(self):
        publication = self.create_publication()
        self.category.is_active = False
        self.category.save()
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse('inventory:reception_detail', args=[publication.pk]),
            self.reception_data(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'La categoría debe estar activa, incluidos sus niveles superiores.',
        )
        self.assertFalse(InventoryItem.objects.filter(publication=publication).exists())

    def test_rejected_publication_can_later_be_accepted_once(self):
        publication = self.create_publication()
        self.client.force_login(self.operator)
        url = reverse('inventory:reception_detail', args=[publication.pk])

        self.client.post(
            url,
            self.reception_data(
                decision=ReceptionInspection.Decision.REJECT,
                notes='Falta una pieza.',
            ),
        )
        response = self.client.post(url, self.reception_data())

        self.assertRedirects(response, url)
        self.assertEqual(publication.reception_inspections.count(), 2)
        self.assertEqual(InventoryItem.objects.filter(publication=publication).count(), 1)

    def test_accepted_reception_is_not_visible_to_another_center(self):
        publication = self.create_publication()
        inspect_reception(
            publication_id=publication.pk,
            operator=self.operator,
            center=self.center,
            decision=ReceptionInspection.Decision.ACCEPT,
            condition=ReusableObject.Condition.GOOD,
            notes='Objeto aceptado.',
        )
        self.client.force_login(self.other_operator)

        response = self.client.get(
            reverse('inventory:reception_detail', args=[publication.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_existing_inventory_without_inspection_is_treated_as_already_received(self):
        publication = self.create_publication()
        item = InventoryItem.objects.create(
            reference='LEGACY-RECEIVED-1',
            owner=self.owner,
            publication=publication,
            center=self.center,
            title=publication.title,
            description=publication.description,
            category=self.category.name,
            category_node=self.category,
            condition=ReusableObject.Condition.GOOD,
            status=ReusableObject.Status.AVAILABLE,
            received_at=timezone.now(),
            validated_at=timezone.now(),
            validated_by=self.operator,
        )
        self.client.force_login(self.operator)
        url = reverse('inventory:reception_detail', args=[publication.pk])

        response = self.client.get(url)
        retry_response = self.client.post(url, self.reception_data())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, item.reference)
        self.assertContains(response, reverse('catalog:detail', args=[item.reference]))
        self.assertNotContains(response, 'Registrar inspección')
        self.assertEqual(retry_response.status_code, 200)
        self.assertEqual(InventoryItem.objects.filter(publication=publication).count(), 1)
        self.assertFalse(ReceptionInspection.objects.filter(publication=publication).exists())
