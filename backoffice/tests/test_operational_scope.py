from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCenterAccess
from backoffice.models import StaffInvitation
from backoffice.services import assign_staff_role
from catalog.models import Category, ReusableObject
from core.roles import ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER
from inventory.models import InventoryItem
from locations.models import RecyclingCenter
from moderation.models import ModerationDecision
from publications.models import Publication
from reservations.models import Reservation


class OperationalScopeTests(TestCase):
    password = 'scope-test-password'

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin_group, _ = Group.objects.get_or_create(name=ROLE_STAFF_ADMIN)
        cls.manager_group, _ = Group.objects.get_or_create(name=ROLE_STAFF_MANAGER)
        cls.admin_group.permissions.add(
            *Permission.objects.filter(
                content_type__app_label__in=('accounts', 'configuration'),
                codename__in=(
                    'manage_staff',
                    'view_user',
                    'manage_email_configuration',
                ),
            )
        )
        cls.center_a = RecyclingCenter.objects.create(
            name='Punto Limpio A',
            slug='scope-center-a',
        )
        cls.center_b = RecyclingCenter.objects.create(
            name='Punto Limpio B',
            slug='scope-center-b',
        )
        cls.admin = User.objects.create_user(
            email='scope-admin@example.com',
            first_name='Admin',
            password=cls.password,
            is_staff=True,
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.admin.groups.add(cls.admin_group)
        cls.manager_without_center = User.objects.create_user(
            email='scope-manager-empty@example.com',
            first_name='Gestor vacío',
            password=cls.password,
            is_staff=True,
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.manager_without_center.groups.add(cls.manager_group)
        cls.manager_a = User.objects.create_user(
            email='scope-manager-a@example.com',
            first_name='Gestor A',
            password=cls.password,
            is_staff=True,
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.manager_a.groups.add(cls.manager_group)
        UserCenterAccess.objects.create(
            user=cls.manager_a,
            center=cls.center_a,
            role=UserCenterAccess.ScopeRole.SUPERVISOR,
        )
        cls.citizen_a = User.objects.create_user(
            email='scope-citizen-a@example.com',
            first_name='Ciudadana A',
            password=cls.password,
            account_status=User.AccountStatus.ACTIVE,
            habitual_recycling_center=cls.center_a,
        )
        cls.citizen_b = User.objects.create_user(
            email='scope-citizen-b@example.com',
            first_name='Ciudadano B',
            password=cls.password,
            account_status=User.AccountStatus.ACTIVE,
            habitual_recycling_center=cls.center_b,
        )
        cls.requester = User.objects.create_user(
            email='scope-requester@example.com',
            first_name='Solicitante',
            password=cls.password,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.category = Category.objects.create(
            name='Categoría de ámbito',
            slug='scope-category',
        )
        cls.pending_a = cls._publication(
            cls.citizen_a,
            'Pendiente A',
            Publication.Status.PENDING_REVIEW,
        )
        cls.pending_b = cls._publication(
            cls.citizen_b,
            'Pendiente B',
            Publication.Status.PENDING_REVIEW,
        )
        cls.approved_a = cls._publication(
            cls.citizen_a,
            'Recepción A',
            Publication.Status.APPROVED,
        )
        cls.approved_b = cls._publication(
            cls.citizen_b,
            'Recepción B',
            Publication.Status.APPROVED,
        )
        pickup_publication_a = cls._publication(
            cls.citizen_a,
            'Recogida A',
            Publication.Status.APPROVED,
        )
        pickup_publication_b = cls._publication(
            cls.citizen_b,
            'Recogida B',
            Publication.Status.APPROVED,
        )
        cls.item_a = cls._item(pickup_publication_a, cls.center_a, 'SCOPE-A')
        cls.item_b = cls._item(pickup_publication_b, cls.center_b, 'SCOPE-B')
        cls.reservation_a = Reservation.objects.create(
            inventory_item=cls.item_a,
            user=cls.requester,
            expires_at=timezone.now() + timedelta(days=1),
        )
        cls.reservation_b = Reservation.objects.create(
            inventory_item=cls.item_b,
            user=cls.requester,
            expires_at=timezone.now() + timedelta(days=1),
        )

    @classmethod
    def _publication(cls, submitter, title, status):
        now = timezone.now()
        return Publication.objects.create(
            submitter=submitter,
            title=title,
            description=f'Descripción de {title}',
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

    def login(self, user):
        self.client.force_login(user)

    def test_01_administrator_can_access_dashboard(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('backoffice:dashboard')).status_code, 200)

    def test_02_administrator_can_access_operation(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('moderation:queue')).status_code, 200)

    def test_03_administrator_can_access_administration(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('backoffice:team_list')).status_code, 200)

    def test_04_administrator_can_access_configuration(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('configuration:email')).status_code, 200)

    def test_05_administrator_sees_data_from_every_center(self):
        self.login(self.admin)
        response = self.client.get(reverse('moderation:queue'))
        self.assertContains(response, self.pending_a.title)
        self.assertContains(response, self.pending_b.title)

    def test_06_administrator_assignment_does_not_restrict_scope(self):
        UserCenterAccess.objects.create(
            user=self.admin,
            center=self.center_a,
            role=UserCenterAccess.ScopeRole.SUPERVISOR,
        )
        self.login(self.admin)
        response = self.client.get(reverse('moderation:detail', args=[self.pending_b.pk]))
        self.assertEqual(response.status_code, 200)

    def test_07_manager_without_center_can_log_in(self):
        self.assertTrue(
            self.client.login(
                username=self.manager_without_center.email,
                password=self.password,
            )
        )

    def test_08_manager_without_center_login_redirects_to_assignment_page(self):
        response = self.client.post(
            reverse('login'),
            {'username': self.manager_without_center.email, 'password': self.password},
        )
        self.assertRedirects(response, reverse('backoffice:no_assignment'))

    def test_09_manager_without_center_can_access_assignment_page(self):
        self.login(self.manager_without_center)
        response = self.client.get(reverse('backoffice:no_assignment'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No tienes ningún punto limpio asignado')

    def test_10_manager_without_center_can_log_out(self):
        self.login(self.manager_without_center)
        response = self.client.post(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def assert_no_scope_redirect(self, url):
        self.login(self.manager_without_center)
        self.assertRedirects(
            self.client.get(url),
            reverse('backoffice:no_assignment'),
        )

    def test_11_manager_without_center_cannot_access_summary(self):
        self.assert_no_scope_redirect(reverse('backoffice:dashboard'))

    def test_12_manager_without_center_cannot_access_moderation(self):
        self.assert_no_scope_redirect(reverse('moderation:queue'))

    def test_13_manager_without_center_cannot_access_reception(self):
        self.assert_no_scope_redirect(reverse('inventory:reception_queue'))

    def test_14_manager_without_center_cannot_access_pickups(self):
        self.assert_no_scope_redirect(reverse('backoffice:pickup_queue'))

    def test_15_manager_without_center_cannot_access_administration(self):
        self.assert_no_scope_redirect(reverse('backoffice:team_list'))

    def test_16_manager_without_center_cannot_access_configuration(self):
        self.assert_no_scope_redirect(reverse('configuration:email'))

    def test_17_manager_with_center_can_access_summary(self):
        self.login(self.manager_a)
        self.assertEqual(self.client.get(reverse('backoffice:dashboard')).status_code, 200)

    def test_18_manager_sees_only_operation_menu(self):
        self.login(self.manager_a)
        response = self.client.get(reverse('backoffice:dashboard'))
        self.assertContains(response, 'Operación')
        for url in (
            reverse('moderation:queue'),
            reverse('inventory:reception_queue'),
            reverse('backoffice:pickup_queue'),
        ):
            self.assertContains(response, url)

    def test_19_manager_does_not_see_administration(self):
        self.login(self.manager_a)
        response = self.client.get(reverse('backoffice:dashboard'))
        self.assertNotContains(response, '<p class="staff-nav__title">Administración</p>', html=True)

    def test_20_manager_does_not_see_configuration(self):
        self.login(self.manager_a)
        response = self.client.get(reverse('backoffice:dashboard'))
        self.assertNotContains(response, reverse('configuration:email'))

    def test_manager_with_center_is_denied_direct_administrative_urls(self):
        self.login(self.manager_a)
        for url in (
            reverse('backoffice:user_list'),
            reverse('backoffice:team_list'),
            reverse('configuration:email'),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_21_manager_kpis_only_count_assigned_center(self):
        self.login(self.manager_a)
        response = self.client.get(reverse('backoffice:dashboard'))
        metrics = {metric['label']: metric['value'] for metric in response.context['metrics']}
        self.assertEqual(metrics['Por moderar'], 1)
        self.assertEqual(metrics['Pendientes de recepción'], 1)
        self.assertEqual(metrics['Recogidas activas'], 1)

    def test_22_manager_moderation_only_lists_assigned_center(self):
        self.login(self.manager_a)
        response = self.client.get(reverse('moderation:queue'))
        self.assertContains(response, self.pending_a.title)
        self.assertNotContains(response, self.pending_b.title)

    def test_23_manager_reception_only_lists_assigned_center(self):
        self.login(self.manager_a)
        response = self.client.get(reverse('inventory:reception_queue'))
        self.assertContains(response, self.approved_a.title)
        self.assertNotContains(response, self.approved_b.title)

    def test_24_manager_pickups_only_list_assigned_center(self):
        self.login(self.manager_a)
        response = self.client.get(reverse('backoffice:pickup_queue'))
        self.assertContains(response, self.item_a.title)
        self.assertNotContains(response, self.item_b.title)

    def test_25_manager_can_access_resource_in_assigned_center(self):
        self.login(self.manager_a)
        response = self.client.get(reverse('moderation:detail', args=[self.pending_a.pk]))
        self.assertEqual(response.status_code, 200)

    def test_26_manager_cannot_access_other_center_detail(self):
        self.login(self.manager_a)
        response = self.client.get(reverse('moderation:detail', args=[self.pending_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_27_manager_cannot_post_to_other_center_resource(self):
        self.login(self.manager_a)
        response = self.client.post(
            reverse('moderation:decide', args=[self.pending_b.pk]),
            {'decision': ModerationDecision.Decision.APPROVE, 'notes': ''},
        )
        self.pending_b.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.pending_b.status, Publication.Status.PENDING_REVIEW)

    def test_28_manager_ajax_request_cannot_reach_other_center(self):
        self.login(self.manager_a)
        response = self.client.get(
            reverse('moderation:detail', args=[self.pending_b.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 404)

    def test_29_manager_forms_do_not_offer_other_centers(self):
        self.login(self.manager_a)
        moderation_response = self.client.get(reverse('moderation:queue'))
        reception_response = self.client.get(
            reverse('inventory:reception_detail', args=[self.approved_a.pk])
        )
        self.assertQuerySetEqual(
            moderation_response.context['filter_form'].fields['center'].queryset,
            [self.center_a],
        )
        self.assertQuerySetEqual(
            reception_response.context['form'].fields['center'].queryset,
            [self.center_a],
        )

    def test_30_manager_cannot_have_more_than_one_active_center(self):
        with self.assertRaises(ValidationError):
            UserCenterAccess.objects.create(
                user=self.manager_a,
                center=self.center_b,
                role=UserCenterAccess.ScopeRole.SUPERVISOR,
            )

    def test_31_removing_assignment_revokes_access_next_request(self):
        self.login(self.manager_a)
        UserCenterAccess.objects.filter(user=self.manager_a).update(is_active=False)
        self.assertRedirects(
            self.client.get(reverse('backoffice:dashboard')),
            reverse('backoffice:no_assignment'),
        )

    def test_32_assigning_center_enables_scope_next_request(self):
        self.login(self.manager_without_center)
        assign_staff_role(
            self.manager_without_center,
            StaffInvitation.Role.MANAGER,
            center=self.center_a,
        )
        self.assertEqual(self.client.get(reverse('backoffice:dashboard')).status_code, 200)

    def test_33_changing_center_revokes_old_scope_immediately(self):
        self.login(self.manager_a)
        assign_staff_role(
            self.manager_a,
            StaffInvitation.Role.MANAGER,
            center=self.center_b,
        )
        self.assertEqual(
            self.client.get(reverse('moderation:detail', args=[self.pending_a.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse('moderation:detail', args=[self.pending_b.pk])).status_code,
            200,
        )

    def test_34_manager_can_operate_qr_from_assigned_center(self):
        self.login(self.manager_a)
        response = self.client.get(
            reverse('reservations:pickup_scan', args=[self.reservation_a.qr_token()])
        )
        self.assertEqual(response.status_code, 200)

    def test_35_manager_cannot_operate_valid_qr_from_other_center(self):
        self.login(self.manager_a)
        response = self.client.get(
            reverse('reservations:pickup_scan', args=[self.reservation_b.qr_token()])
        )
        self.assertEqual(response.status_code, 403)

    def test_36_administrator_can_operate_qr_from_both_centers(self):
        self.login(self.admin)
        for reservation in (self.reservation_a, self.reservation_b):
            with self.subTest(reservation=reservation.pk):
                response = self.client.get(
                    reverse('reservations:pickup_scan', args=[reservation.qr_token()])
                )
                self.assertEqual(response.status_code, 200)
