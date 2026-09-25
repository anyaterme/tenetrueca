from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.models import AuditEvent
from configuration.forms import EmailConfigurationForm
from configuration.models import EmailConfiguration
from configuration.services import EmailConfigurationService
from core.roles import ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST='smtp.environment.test',
    EMAIL_PORT=587,
    EMAIL_HOST_USER='environment-user',
    EMAIL_HOST_PASSWORD='environment-secret',
    EMAIL_USE_TLS=True,
    EMAIL_USE_SSL=False,
    EMAIL_TIMEOUT=12,
    DEFAULT_FROM_EMAIL='Environment <environment@example.com>',
    EMAIL_SETTINGS_ENCRYPTION_KEY='email-configuration-test-key',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class EmailConfigurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        admin_group, _ = Group.objects.get_or_create(name=ROLE_STAFF_ADMIN)
        manager_group, _ = Group.objects.get_or_create(name=ROLE_STAFF_MANAGER)
        admin_group.permissions.add(
            Permission.objects.get(
                content_type__app_label='configuration',
                codename='manage_email_configuration',
            )
        )
        cls.admin = User.objects.create_user(
            email='email-admin@example.com',
            first_name='Ada',
            password='test-password',
            is_staff=True,
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.admin.groups.add(admin_group)
        cls.manager = User.objects.create_user(
            email='email-manager@example.com',
            first_name='Marta',
            password='test-password',
            is_staff=True,
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.manager.groups.add(manager_group)
        cls.citizen = User.objects.create_user(
            email='email-citizen@example.com',
            first_name='Celia',
            password='test-password',
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def _save(self, **overrides):
        data = {'password_action': EmailConfigurationForm.PASSWORD_INHERIT}
        for field_name, value in overrides.items():
            data[f'override_{field_name}'] = 'on'
            data[field_name] = value
        return self.client.post(reverse('configuration:email'), data)

    def test_only_administrators_can_access_email_configuration(self):
        self.assertEqual(
            self.client.get(reverse('configuration:email')).status_code,
            200,
        )
        self.client.force_login(self.manager)
        self.assertRedirects(
            self.client.get(reverse('configuration:email')),
            reverse('backoffice:no_assignment'),
        )
        self.client.force_login(self.citizen)
        self.assertEqual(
            self.client.get(reverse('configuration:email')).status_code,
            403,
        )

    def test_without_overrides_all_values_come_from_settings(self):
        effective = EmailConfigurationService.effective()

        self.assertEqual(effective.host, 'smtp.environment.test')
        self.assertEqual(effective.port, 587)
        self.assertEqual(effective.username, 'environment-user')
        self.assertEqual(effective.password, 'environment-secret')
        self.assertEqual(effective.default_from_email, 'Environment <environment@example.com>')
        self.assertEqual(effective.timeout, 12)
        self.assertTrue(effective.use_tls)
        self.assertFalse(effective.use_ssl)
        self.assertTrue(
            all(source == 'environment' for source in effective.sources.values())
        )

    def test_database_override_wins_and_other_fields_keep_inheriting(self):
        EmailConfiguration.objects.create(email_host='smtp.database.test')

        effective = EmailConfigurationService.effective()

        self.assertEqual(effective.host, 'smtp.database.test')
        self.assertEqual(effective.port, 587)
        self.assertEqual(effective.username, 'environment-user')
        self.assertEqual(effective.sources['EMAIL_HOST'], 'database')
        self.assertEqual(effective.sources['EMAIL_PORT'], 'environment')

    def test_saving_one_field_does_not_copy_environment_values_to_database(self):
        response = self._save(email_host='smtp.only-override.test')

        self.assertRedirects(response, reverse('configuration:email'))
        record = EmailConfiguration.objects.get()
        self.assertEqual(record.email_host, 'smtp.only-override.test')
        self.assertIsNone(record.email_port)
        self.assertIsNone(record.email_host_user)
        self.assertIsNone(record.security)
        self.assertIsNone(record.default_from_email)
        self.assertIsNone(record.email_timeout)

    def test_security_modes_are_mutually_exclusive(self):
        record = EmailConfiguration.objects.create()
        expected = {
            EmailConfiguration.Security.STARTTLS: (True, False),
            EmailConfiguration.Security.SSL_TLS: (False, True),
            EmailConfiguration.Security.NONE: (False, False),
        }
        for security, flags in expected.items():
            record.security = security
            record.save(update_fields=['security'])
            effective = EmailConfigurationService.effective()
            self.assertEqual((effective.use_tls, effective.use_ssl), flags)
            self.assertFalse(effective.use_tls and effective.use_ssl)

    @override_settings(EMAIL_USE_TLS=True, EMAIL_USE_SSL=True)
    def test_invalid_environment_security_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            EmailConfigurationService.effective()

    def test_password_is_encrypted_and_never_returned_to_the_form(self):
        secret = 'smtp-secret-that-must-not-leak'
        response = self.client.post(
            reverse('configuration:email'),
            {
                'password_action': EmailConfigurationForm.PASSWORD_REPLACE,
                'new_password': secret,
            },
        )

        self.assertRedirects(response, reverse('configuration:email'))
        record = EmailConfiguration.objects.get()
        self.assertNotEqual(record.email_host_password_encrypted, secret)
        self.assertNotIn(secret, record.email_host_password_encrypted)
        self.assertEqual(EmailConfigurationService.effective().password, secret)
        page = self.client.get(reverse('configuration:email'))
        self.assertNotContains(page, secret)
        self.assertContains(page, 'Existe una credencial cifrada almacenada')

    def test_changing_and_keeping_password_preserves_other_overrides(self):
        record = EmailConfiguration.objects.create(
            email_host='smtp.persisted.test',
            email_port=2525,
            email_host_password_encrypted=EmailConfigurationService.encrypt_password(
                'first-secret'
            ),
        )
        encrypted_before = record.email_host_password_encrypted

        self.client.post(
            reverse('configuration:email'),
            {
                'override_email_host': 'on',
                'email_host': 'smtp.persisted.test',
                'override_email_port': 'on',
                'email_port': 2525,
                'password_action': EmailConfigurationForm.PASSWORD_KEEP,
            },
        )
        record.refresh_from_db()
        self.assertEqual(record.email_host_password_encrypted, encrypted_before)

        self.client.post(
            reverse('configuration:email'),
            {
                'override_email_host': 'on',
                'email_host': 'smtp.persisted.test',
                'override_email_port': 'on',
                'email_port': 2525,
                'password_action': EmailConfigurationForm.PASSWORD_REPLACE,
                'new_password': 'second-secret',
            },
        )
        record.refresh_from_db()
        self.assertEqual(record.email_host, 'smtp.persisted.test')
        self.assertEqual(record.email_port, 2525)
        self.assertEqual(EmailConfigurationService.effective().password, 'second-secret')

    def test_removing_password_override_falls_back_to_environment(self):
        EmailConfiguration.objects.create(
            email_host_password_encrypted=EmailConfigurationService.encrypt_password(
                'database-secret'
            )
        )

        response = self.client.post(
            reverse('configuration:email'),
            {'password_action': EmailConfigurationForm.PASSWORD_INHERIT},
        )

        self.assertRedirects(response, reverse('configuration:email'))
        self.assertFalse(EmailConfiguration.objects.exists())
        self.assertEqual(
            EmailConfigurationService.effective().password,
            'environment-secret',
        )

    def test_reset_deletes_overrides_and_restores_environment(self):
        EmailConfiguration.objects.create(
            email_host='smtp.database.test',
            email_port=465,
            security=EmailConfiguration.Security.SSL_TLS,
        )

        response = self.client.post(reverse('configuration:email_reset'))

        self.assertRedirects(response, reverse('configuration:email'))
        self.assertFalse(EmailConfiguration.objects.exists())
        effective = EmailConfigurationService.effective()
        self.assertEqual(effective.host, 'smtp.environment.test')
        self.assertEqual(effective.port, 587)
        self.assertTrue(effective.use_tls)

    def test_test_email_uses_effective_configuration(self):
        EmailConfiguration.objects.create(
            default_from_email='Backoffice <backoffice@example.com>',
        )

        response = self.client.post(
            reverse('configuration:email_test'),
            {'recipient': 'recipient@example.com'},
        )

        self.assertRedirects(response, reverse('configuration:email'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, 'Prueba de correo de Tenetrueca')
        self.assertEqual(mail.outbox[0].from_email, 'Backoffice <backoffice@example.com>')
        self.assertEqual(mail.outbox[0].to, ['recipient@example.com'])
        self.assertTrue(
            AuditEvent.objects.filter(
                action='configuration.email.test_sent',
                actor=self.admin,
            ).exists()
        )

    def test_smtp_errors_and_audit_never_reveal_secrets(self):
        secret = 'never-expose-this-secret'
        EmailConfiguration.objects.create(
            email_host_password_encrypted=EmailConfigurationService.encrypt_password(secret)
        )

        with patch(
            'configuration.services.EmailMultiAlternatives.send',
            side_effect=RuntimeError(f'failure with {secret}'),
        ):
            response = self.client.post(
                reverse('configuration:email_test'),
                {'recipient': 'recipient@example.com'},
                follow=True,
            )

        self.assertNotContains(response, secret)
        self.assertContains(response, 'No se pudo enviar el correo de prueba')
        event = AuditEvent.objects.get(action='configuration.email.test_failed')
        self.assertEqual(event.result, 'failure')
        self.assertNotIn(secret, str(event.metadata))

    def test_updates_and_password_changes_are_audited_without_values(self):
        secret = 'audit-secret-value'
        self.client.post(
            reverse('configuration:email'),
            {
                'override_email_host': 'on',
                'email_host': 'smtp.audited.test',
                'password_action': EmailConfigurationForm.PASSWORD_REPLACE,
                'new_password': secret,
            },
        )

        events = AuditEvent.objects.filter(
            action__in=(
                'configuration.email.updated',
                'configuration.email.password_changed',
            )
        )
        self.assertEqual(events.count(), 2)
        self.assertNotIn(secret, str(list(events.values('before', 'after', 'metadata'))))
        updated = events.get(action='configuration.email.updated')
        self.assertIn('EMAIL_HOST', updated.metadata['changed_fields'])
        self.assertIn('EMAIL_HOST_PASSWORD', updated.metadata['changed_fields'])

    def test_reset_and_password_removal_are_audited(self):
        EmailConfiguration.objects.create(
            email_host='smtp.database.test',
            email_host_password_encrypted=EmailConfigurationService.encrypt_password(
                'database-secret'
            ),
        )

        self.client.post(reverse('configuration:email_reset'))

        self.assertTrue(
            AuditEvent.objects.filter(action='configuration.email.reset').exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action='configuration.email.password_removed'
            ).exists()
        )

    def test_get_cannot_modify_reset_or_send_test(self):
        EmailConfiguration.objects.create(email_host='smtp.database.test')

        self.assertEqual(
            self.client.get(reverse('configuration:email_reset')).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(reverse('configuration:email_test')).status_code,
            405,
        )
        self.assertTrue(EmailConfiguration.objects.exists())
