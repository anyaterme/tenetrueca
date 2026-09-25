from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from locations.models import RecyclingCenter


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ProfileTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='perfil@example.com',
            first_name='Perfil',
            last_name='Inicial',
            nif_nie='11111111H',
            password='Clave-perfil-2026',
        )
        self.other_user = get_user_model().objects.create_user(
            email='otra@example.com',
            first_name='Otra',
            nif_nie='22222222J',
            password='Clave-otra-2026',
        )
        self.open_center = RecyclingCenter.objects.create(
            name='Punto Limpio Abierto',
            slug='punto-abierto',
        )
        self.closed_center = RecyclingCenter.objects.create(
            name='Punto Limpio Cerrado',
            slug='punto-cerrado',
            operational_status=RecyclingCenter.OperationalStatus.CLOSED,
        )
        self.client.force_login(self.user)

    def test_user_can_view_profile(self):
        response = self.client.get(reverse('profile'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Datos personales')
        self.assertContains(response, 'Información de cuenta')
        self.assertContains(response, self.user.first_name)
        self.assertContains(response, self.user.last_name)
        self.assertContains(response, self.user.nif_nie)
        self.assertContains(response, self.user.email)
        self.assertNotContains(response, 'Origen de autenticación')
        self.assertNotContains(response, self.user.external_auth_id or 'external-auth-secret')

    def test_unauthenticated_user_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.get(reverse('profile'))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('profile')}",
        )

    def test_empty_profile_values_use_explicit_labels(self):
        self.user.last_name = ''
        self.user.nif_nie = ''
        self.user.phone = ''
        self.user.habitual_recycling_center = None
        self.user.save(
            update_fields=['last_name', 'nif_nie', 'phone', 'habitual_recycling_center']
        )

        response = self.client.get(reverse('profile'))

        self.assertContains(response, 'No informado', count=3)
        self.assertContains(response, 'Sin seleccionar')

    def test_account_navigation_uses_named_urls_and_marks_current_page(self):
        response = self.client.get(reverse('profile'))

        self.assertContains(response, f'href="{reverse("profile")}"')
        self.assertContains(response, f'href="{reverse("profile-edit")}"')
        self.assertContains(response, f'href="{reverse("preferences")}"')
        self.assertContains(response, f'href="{reverse("password-change")}"')
        self.assertContains(
            response,
            'class="account-nav__item is-active" href="/cuenta/perfil/" aria-current="page"',
        )

        for url_name in ('profile-edit', 'preferences', 'password-change'):
            with self.subTest(url_name=url_name):
                page_response = self.client.get(reverse(url_name))
                self.assertContains(
                    page_response,
                    f'class="account-nav__item is-active" href="{reverse(url_name)}" '
                    'aria-current="page"',
                )

    def test_user_can_edit_allowed_fields_and_open_center(self):
        response = self.client.post(
            reverse('profile-edit'),
            {
                'first_name': 'Nombre nuevo',
                'last_name': 'Apellido nuevo',
                'phone': '600123123',
                'habitual_recycling_center': self.open_center.pk,
                'email': 'cambiado@example.com',
                'nif_nie': '99999999R',
            },
        )

        self.assertRedirects(response, reverse('profile'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Nombre nuevo')
        self.assertEqual(self.user.habitual_recycling_center, self.open_center)
        self.assertEqual(self.user.email, 'perfil@example.com')
        self.assertEqual(self.user.nif_nie, '11111111H')

    def test_edit_profile_requires_authentication(self):
        self.client.logout()

        response = self.client.get(reverse('profile-edit'))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('profile-edit')}",
        )

    def test_edit_profile_displays_current_values_and_locked_identity(self):
        response = self.client.get(reverse('profile-edit'))

        self.assertContains(response, self.user.first_name)
        self.assertContains(response, self.user.last_name)
        self.assertContains(response, self.user.email)
        self.assertContains(response, self.user.nif_nie)
        self.assertContains(response, 'Una vez informado, no puede modificarse')
        self.assertNotContains(response, 'name="nif_nie"')

    def test_nif_can_be_added_once_and_is_normalized(self):
        self.user.nif_nie = ''
        self.user.save(update_fields=['nif_nie'])

        first_response = self.client.post(
            reverse('profile-edit'),
            {
                'first_name': self.user.first_name,
                'last_name': self.user.last_name,
                'nif_nie': '  x1234567l  ',
                'phone': '',
                'habitual_recycling_center': '',
            },
        )

        self.assertRedirects(first_response, reverse('profile'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.nif_nie, 'X1234567L')

        second_response = self.client.post(
            reverse('profile-edit'),
            {
                'first_name': self.user.first_name,
                'last_name': self.user.last_name,
                'nif_nie': '99999999R',
                'phone': '',
                'habitual_recycling_center': '',
            },
        )

        self.assertRedirects(second_response, reverse('profile'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.nif_nie, 'X1234567L')

    def test_profile_edit_does_not_mass_assign_internal_fields(self):
        response = self.client.post(
            reverse('profile-edit'),
            {
                'first_name': 'Propio',
                'last_name': 'Usuario',
                'phone': '600000000',
                'habitual_recycling_center': self.open_center.pk,
                'email': 'ataque@example.com',
                'external_auth_id': 'external-inyectado',
                'auth_source': self.user.AuthSource.EXTERNAL,
                'is_staff': 'on',
                'is_superuser': 'on',
                'account_status': self.user.AccountStatus.SUSPENDED,
            },
        )

        self.assertRedirects(response, reverse('profile'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'perfil@example.com')
        self.assertIsNone(self.user.external_auth_id)
        self.assertEqual(self.user.auth_source, self.user.AuthSource.LOCAL)
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertNotEqual(self.user.account_status, self.user.AccountStatus.SUSPENDED)

    def test_closed_center_is_not_selectable(self):
        response = self.client.get(reverse('profile-edit'))

        self.assertContains(response, self.open_center.name)
        self.assertNotContains(response, self.closed_center.name)

    def test_user_cannot_edit_another_user(self):
        self.client.post(
            reverse('profile-edit'),
            {
                'first_name': 'Propio',
                'last_name': 'Usuario',
                'phone': '',
                'habitual_recycling_center': '',
                'user_id': self.other_user.pk,
            },
        )

        self.other_user.refresh_from_db()
        self.assertEqual(self.other_user.first_name, 'Otra')

    def test_preferences_preserve_other_values(self):
        self.user.preferences = {'language': 'es'}
        self.user.save(update_fields=['preferences'])

        response = self.client.post(
            reverse('preferences'),
            {'optional_email_communications': 'on'},
        )

        self.assertRedirects(response, reverse('preferences'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.preferences['optional_email_communications'])
        self.assertEqual(self.user.preferences['language'], 'es')

    def test_preferences_require_authentication(self):
        self.client.logout()

        response = self.client.get(reverse('preferences'))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('preferences')}",
        )

    def test_preferences_post_only_updates_current_user(self):
        self.other_user.preferences = {'optional_email_communications': False}
        self.other_user.save(update_fields=['preferences'])

        response = self.client.post(
            reverse('preferences'),
            {
                'optional_email_communications': 'on',
                'user_id': self.other_user.pk,
            },
        )

        self.assertRedirects(response, reverse('preferences'))
        self.user.refresh_from_db()
        self.other_user.refresh_from_db()
        self.assertTrue(self.user.preferences['optional_email_communications'])
        self.assertFalse(self.other_user.preferences['optional_email_communications'])
