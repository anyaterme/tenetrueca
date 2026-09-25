import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.sessions.models import Session
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from backoffice.models import StaffAuditEvent, StaffInvitation
from backoffice.forms import StaffInviteForm
from core.roles import ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@tenetrueca.test',
    AUTH_PROVIDER='local',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class StaffManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin_group, _ = Group.objects.get_or_create(name=ROLE_STAFF_ADMIN)
        cls.manager_group, _ = Group.objects.get_or_create(name=ROLE_STAFF_MANAGER)
        cls.admin_group.permissions.add(
            Permission.objects.get(
                content_type__app_label='accounts',
                codename='manage_staff',
            )
        )
        cls.admin = User.objects.create_user(
            email='admin-team@example.com',
            first_name='Ada',
            password='test-password',
            is_staff=True,
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.admin.groups.add(cls.admin_group)
        cls.second_admin = User.objects.create_user(
            email='second-admin-team@example.com',
            first_name='Alex',
            password='test-password',
            is_staff=True,
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.second_admin.groups.add(cls.admin_group)
        cls.manager = User.objects.create_user(
            email='manager-team@example.com',
            first_name='Marta',
            password='test-password',
            is_staff=True,
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.manager.groups.add(cls.manager_group)
        cls.citizen = User.objects.create_user(
            email='citizen-team@example.com',
            first_name='Celia',
            password='test-password',
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_team_access_is_limited_to_administrators(self):
        response = self.client.get(reverse('backoffice:team_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Equipo')

        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(reverse('backoffice:team_list')).status_code,
            403,
        )

        self.client.force_login(self.citizen)
        self.assertEqual(
            self.client.get(reverse('backoffice:team_list')).status_code,
            403,
        )

    def test_invitation_creates_staff_without_a_known_password(self):
        response = self.client.post(
            reverse('backoffice:team_add'),
            {
                'first_name': 'Nora',
                'last_name': 'Suarez',
                'email': 'NORA.Team@Example.com',
                'role': StaffInvitation.Role.MANAGER,
            },
        )

        user = get_user_model().objects.get(email='nora.team@example.com')
        invitation = StaffInvitation.objects.get(user=user)
        self.assertRedirects(
            response,
            reverse('backoffice:team_detail', args=[user.pk]),
        )
        self.assertTrue(user.is_staff)
        self.assertTrue(user.groups.filter(name=ROLE_STAFF_MANAGER).exists())
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.must_change_password)
        self.assertEqual(len(invitation.token_hash), 64)
        self.assertNotIn(invitation.token_hash, mail.outbox[0].body)
        self.assertEqual(
            set(user.staff_audit_events.values_list('action', flat=True)),
            {
                StaffAuditEvent.Action.ACCESS_GRANTED,
                StaffAuditEvent.Action.INVITED,
            },
        )

    def test_temporary_password_creation_shows_secure_credentials_once(self):
        response = self.client.post(
            reverse('backoffice:team_add'),
            {
                'first_name': 'Tania',
                'last_name': 'Temporal',
                'email': 'tania-temporal@example.com',
                'role': StaffInvitation.Role.MANAGER,
                'provisioning_method': (
                    StaffInviteForm.ProvisioningMethod.TEMPORARY_PASSWORD
                ),
            },
        )

        user = get_user_model().objects.get(email='tania-temporal@example.com')
        temporary_password = response.context['temporary_password']
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            'backoffice/team_temporary_credentials.html',
        )
        self.assertTrue(user.is_staff)
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password(temporary_password))
        self.assertEqual(len(temporary_password), 20)
        self.assertRegex(temporary_password, r'[A-Z]')
        self.assertRegex(temporary_password, r'[a-z]')
        self.assertRegex(temporary_password, r'[0-9]')
        self.assertRegex(temporary_password, r'[!@#$%+\-_]')
        self.assertContains(response, 'data-copy-target="temporary-password"')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(StaffInvitation.objects.filter(user=user).exists())
        event = user.staff_audit_events.get(
            action=StaffAuditEvent.Action.TEMPORARY_PASSWORD_CREATED
        )
        self.assertNotIn(temporary_password, str(event.metadata))

    def test_temporary_password_requires_change_before_backoffice_access(self):
        response = self.client.post(
            reverse('backoffice:team_add'),
            {
                'first_name': 'Teo',
                'last_name': 'Primer acceso',
                'email': 'teo-first-login@example.com',
                'role': StaffInvitation.Role.MANAGER,
                'provisioning_method': (
                    StaffInviteForm.ProvisioningMethod.TEMPORARY_PASSWORD
                ),
            },
        )
        temporary_password = response.context['temporary_password']
        user = get_user_model().objects.get(email='teo-first-login@example.com')
        self.client.logout()

        login_response = self.client.post(
            reverse('login'),
            {'username': user.email, 'password': temporary_password},
        )
        self.assertRedirects(login_response, reverse('password-change'))
        self.assertRedirects(
            self.client.get(reverse('backoffice:dashboard')),
            reverse('password-change'),
        )
        required_page = self.client.get(reverse('password-change'))
        self.assertContains(required_page, 'La contraseña que has utilizado es temporal')

        change_response = self.client.post(
            reverse('password-change'),
            {
                'old_password': temporary_password,
                'new_password1': 'Nueva-clave-segura-del-equipo-2026',
                'new_password2': 'Nueva-clave-segura-del-equipo-2026',
            },
        )

        user.refresh_from_db()
        self.assertRedirects(change_response, reverse('backoffice:dashboard'))
        self.assertFalse(user.must_change_password)
        self.assertTrue(user.check_password('Nueva-clave-segura-del-equipo-2026'))
        self.assertEqual(
            self.client.get(reverse('backoffice:dashboard')).status_code,
            200,
        )

    def test_invitation_link_sets_password_and_is_single_use(self):
        self.client.post(
            reverse('backoffice:team_add'),
            {
                'first_name': 'Leo',
                'last_name': 'Martin',
                'email': 'leo-team@example.com',
                'role': StaffInvitation.Role.MANAGER,
            },
        )
        match = re.search(r'/backoffice/invitacion/([^/\s]+)/', mail.outbox[0].body)
        self.assertIsNotNone(match)
        url = reverse('backoffice:staff_invitation_accept', args=[match.group(1)])
        self.client.logout()

        response = self.client.post(
            url,
            {
                'new_password1': 'A-very-secure-team-password-42',
                'new_password2': 'A-very-secure-team-password-42',
            },
        )

        user = get_user_model().objects.get(email='leo-team@example.com')
        self.assertRedirects(response, reverse('backoffice:dashboard'))
        self.assertTrue(user.check_password('A-very-secure-team-password-42'))
        self.assertIsNotNone(user.staff_invitations.get().accepted_at)
        self.assertEqual(self.client.get(url).status_code, 400)

    def test_existing_account_is_converted_without_duplication(self):
        existing_password_hash = self.citizen.password
        response = self.client.post(
            reverse('backoffice:team_add'),
            {
                'first_name': 'Ignored',
                'last_name': 'Name',
                'email': self.citizen.email.upper(),
                'role': StaffInvitation.Role.MANAGER,
                'provisioning_method': (
                    StaffInviteForm.ProvisioningMethod.TEMPORARY_PASSWORD
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'existing-account-prompt')

        response = self.client.post(
            reverse('backoffice:team_grant_existing'),
            {
                'user_id': self.citizen.pk,
                'role': StaffInvitation.Role.MANAGER,
                'provisioning_method': (
                    StaffInviteForm.ProvisioningMethod.TEMPORARY_PASSWORD
                ),
            },
        )

        self.citizen.refresh_from_db()
        self.assertRedirects(
            response,
            reverse('backoffice:team_detail', args=[self.citizen.pk]),
        )
        self.assertEqual(
            get_user_model().objects.filter(email__iexact=self.citizen.email).count(),
            1,
        )
        self.assertTrue(self.citizen.is_staff)
        self.assertEqual(self.citizen.password, existing_password_hash)
        self.assertFalse(self.citizen.must_change_password)
        self.assertTrue(
            self.citizen.groups.filter(name=ROLE_STAFF_MANAGER).exists()
        )
        self.assertTrue(
            self.citizen.staff_audit_events.filter(
                action=StaffAuditEvent.Action.ACCESS_GRANTED
            ).exists()
        )

    def test_role_change_is_audited_and_applies_immediately(self):
        response = self.client.post(
            reverse('backoffice:team_detail', args=[self.manager.pk]),
            {
                'first_name': self.manager.first_name,
                'last_name': self.manager.last_name,
                'email': self.manager.email,
                'role': StaffInvitation.Role.ADMINISTRATOR,
            },
        )

        self.manager.refresh_from_db()
        self.assertRedirects(
            response,
            reverse('backoffice:team_detail', args=[self.manager.pk]),
        )
        self.assertTrue(self.manager.groups.filter(name=ROLE_STAFF_ADMIN).exists())
        self.assertFalse(
            self.manager.groups.filter(name=ROLE_STAFF_MANAGER).exists()
        )
        self.assertTrue(self.manager.has_perm('accounts.manage_staff'))
        self.assertTrue(
            self.manager.staff_audit_events.filter(
                action=StaffAuditEvent.Action.ROLE_CHANGED,
                metadata__from=StaffInvitation.Role.MANAGER,
                metadata__to=StaffInvitation.Role.ADMINISTRATOR,
            ).exists()
        )

    def test_deactivate_and_reactivate_invalidate_sessions_and_are_audited(self):
        other_client = self.client_class()
        other_client.force_login(self.manager)
        session_key = other_client.session.session_key
        self.assertTrue(Session.objects.filter(session_key=session_key).exists())

        response = self.client.post(
            reverse('backoffice:team_deactivate', args=[self.manager.pk])
        )
        self.manager.refresh_from_db()
        self.assertRedirects(
            response,
            reverse('backoffice:team_detail', args=[self.manager.pk]),
        )
        self.assertFalse(self.manager.is_active)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())

        self.client.post(
            reverse('backoffice:team_reactivate', args=[self.manager.pk])
        )
        self.manager.refresh_from_db()
        self.assertTrue(self.manager.is_active)
        self.assertEqual(
            list(
                self.manager.staff_audit_events.order_by('created_at').values_list(
                    'action', flat=True
                )
            ),
            [
                StaffAuditEvent.Action.DEACTIVATED,
                StaffAuditEvent.Action.ACTIVATED,
            ],
        )

    def test_administrator_cannot_deactivate_self(self):
        response = self.client.post(
            reverse('backoffice:team_deactivate', args=[self.admin.pk]),
            follow=True,
        )

        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertContains(response, 'No puedes desactivar tu propia cuenta.')

    def test_last_active_administrator_cannot_lose_privileges(self):
        self.second_admin.groups.clear()
        self.second_admin.is_staff = False
        self.second_admin.save(update_fields=['is_staff'])

        response = self.client.post(
            reverse('backoffice:team_detail', args=[self.admin.pk]),
            {
                'first_name': self.admin.first_name,
                'last_name': self.admin.last_name,
                'email': self.admin.email,
                'role': StaffInvitation.Role.MANAGER,
            },
        )

        self.admin.refresh_from_db()
        self.assertRedirects(
            response,
            reverse('backoffice:team_detail', args=[self.admin.pk]),
        )
        self.assertTrue(self.admin.groups.filter(name=ROLE_STAFF_ADMIN).exists())
        self.assertFalse(
            self.admin.staff_audit_events.filter(
                action=StaffAuditEvent.Action.ROLE_CHANGED
            ).exists()
        )

    def test_last_active_administrator_cannot_be_removed_by_superuser(self):
        self.second_admin.groups.clear()
        self.second_admin.is_staff = False
        self.second_admin.save(update_fields=['is_staff'])
        emergency_admin = get_user_model().objects.create_superuser(
            email='emergency-admin@example.com',
            first_name='Emergency',
            password='test-password',
        )
        emergency_admin.is_staff = False
        emergency_admin.save(update_fields=['is_staff'])
        self.client.force_login(emergency_admin)

        response = self.client.post(
            reverse('backoffice:team_remove', args=[self.admin.pk]),
            follow=True,
        )

        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff)
        self.assertContains(
            response,
            'El sistema debe conservar al menos un administrador activo.',
        )

    def test_administrator_can_remove_another_admin_only_when_one_remains(self):
        response = self.client.post(
            reverse('backoffice:team_remove', args=[self.second_admin.pk])
        )

        self.second_admin.refresh_from_db()
        self.assertRedirects(response, reverse('backoffice:team_list'))
        self.assertFalse(self.second_admin.is_staff)
        self.assertTrue(
            self.second_admin.staff_audit_events.filter(
                action=StaffAuditEvent.Action.ACCESS_REMOVED
            ).exists()
        )
