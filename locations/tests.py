from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import User, UserCenterAccess
from audit.models import AuditEvent
from core.roles import ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER
from locations.forms import RecyclingCenterForm
from locations.models import RecyclingCenter
from locations.services import center_map_data, public_centers


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CenterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(email='centers-admin@example.com', first_name='Admin', is_staff=True, account_status=User.AccountStatus.ACTIVE)
        cls.admin.groups.add(Group.objects.get(name=ROLE_STAFF_ADMIN))
        cls.manager = User.objects.create_user(email='centers-manager@example.com', first_name='Gestor', is_staff=True, account_status=User.AccountStatus.ACTIVE)
        cls.manager.groups.add(Group.objects.get(name=ROLE_STAFF_MANAGER))
        cls.citizen = User.objects.create_user(email='centers-user@example.com', first_name='Usuario', account_status=User.AccountStatus.ACTIVE)
        cls.center = RecyclingCenter.objects.create(name='Punto de prueba', slug='punto-prueba', address='Calle de prueba, Tenerife', latitude='28.400000', longitude='-16.500000', show_on_public_map=True)
        UserCenterAccess.objects.create(user=cls.manager, center=cls.center, role=UserCenterAccess.ScopeRole.SUPERVISOR)

    def payload(self, **overrides):
        return {'name': 'Punto de prueba', 'slug': 'punto-prueba', 'address': 'Calle de prueba, Tenerife', 'latitude': '28.400000', 'longitude': '-16.500000', 'phone': '900 305 000', 'schedule': 'Lunes a viernes', 'operational_status': 'open', 'public_information': '', 'show_on_public_map': 'on', **overrides}

    def test_public_list_filters_visibility_and_inactive_but_retains_closures(self):
        hidden = RecyclingCenter.objects.create(name='Oculto', slug='oculto')
        inactive = RecyclingCenter.objects.create(name='Inactivo', slug='inactivo', latitude=28, longitude=-16, address='Tenerife', is_active=False, show_on_public_map=True)
        missing = RecyclingCenter.objects.create(name='Sin ubicación', slug='sin-ubicacion', address='Tenerife', show_on_public_map=True)
        self.center.operational_status = 'temporarily_closed'
        self.center.save()
        self.assertEqual(list(public_centers()), [self.center])
        response = self.client.get(reverse('home'))
        self.assertContains(response, self.center.name)
        self.assertContains(response, 'Cierre temporal')
        for center in (hidden, inactive, missing):
            self.assertNotContains(response, center.name)

    @override_settings(GOOGLE_MAPS_API_KEY='test-browser-key', GOOGLE_MAPS_MAP_ID='test-map-id')
    def test_home_serializes_public_fields_safely_and_defers_google_loading(self):
        self.center.email = 'private@example.com'
        self.center.public_information = '</script><script>alert(1)</script>'
        self.center.save()
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Mostrar mapa')
        self.assertNotContains(response, 'src="https://maps.googleapis.com')
        self.assertNotContains(response, 'private@example.com')
        self.assertNotContains(response, '</script><script>alert(1)</script>')
        self.assertContains(response, '\\u003C/script\\u003E')
        serialized = center_map_data([self.center])[0]
        self.assertEqual(serialized['lat'], 28.4)
        self.assertNotIn('email', serialized)
        self.assertIn('destination=28.400000%2C-16.500000', serialized['directionsUrl'])

    @override_settings(GOOGLE_MAPS_API_KEY='', GOOGLE_MAPS_MAP_ID='')
    def test_home_without_maps_config_still_offers_directions(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, self.center.name)
        self.assertContains(response, 'Cómo llegar')
        self.assertNotContains(response, 'data-map-load')

    def test_validation_requires_address_and_paired_valid_coordinates_to_publish(self):
        for overrides, field in (({'address': ''}, 'address'), ({'latitude': '', 'longitude': ''}, 'show_on_public_map'), ({'latitude': ''}, 'latitude'), ({'latitude': '91'}, 'latitude'), ({'longitude': '-181'}, 'longitude')):
            with self.subTest(overrides=overrides):
                form = RecyclingCenterForm(self.payload(**overrides), instance=self.center)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)
        form = RecyclingCenterForm(self.payload(show_on_public_map='', latitude='', longitude=''), instance=self.center)
        self.assertTrue(form.is_valid(), form.errors)
        form = RecyclingCenterForm(self.payload(latitude='0', longitude='0'), instance=self.center)
        self.assertTrue(form.is_valid(), form.errors)

    def test_only_administrators_with_permissions_can_manage_centers(self):
        urls = (reverse('locations:manage_list'), reverse('locations:create'), reverse('locations:edit', args=[self.center.pk]))
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        self.manager.user_permissions.add(*Permission.objects.filter(content_type__app_label='locations'))
        for user in (self.manager, self.citizen):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, self.payload()).status_code, 403)
        self.client.force_login(self.admin)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_edit_hides_center_without_changing_operational_relations_and_audits(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('locations:edit', args=[self.center.pk]), self.payload(show_on_public_map=''))
        self.assertRedirects(response, reverse('locations:manage_list'))
        self.center.refresh_from_db()
        self.assertFalse(self.center.show_on_public_map)
        self.assertTrue(self.center.is_active)
        self.assertEqual(self.center.operational_status, 'open')
        self.assertTrue(UserCenterAccess.objects.filter(user=self.manager, center=self.center, is_active=True).exists())
        event = AuditEvent.objects.get(action='center.updated')
        self.assertTrue(event.before['show_on_public_map'])
        self.assertFalse(event.after['show_on_public_map'])
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.after['latitude'], '28.400000')

    def test_create_and_invalid_edit(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('locations:create'), self.payload(name='Nuevo centro', slug='nuevo-centro'))
        self.assertRedirects(response, reverse('locations:manage_list'))
        self.assertTrue(RecyclingCenter.objects.filter(slug='nuevo-centro').exists())
        self.assertTrue(AuditEvent.objects.filter(action='center.created').exists())
        response = self.client.post(reverse('locations:edit', args=[self.center.pk]), self.payload(latitude='100'))
        self.assertEqual(response.status_code, 200)
        self.center.refresh_from_db()
        self.assertEqual(self.center.latitude, Decimal('28.400000'))
        self.assertFalse(AuditEvent.objects.filter(action='center.updated').exists())

    def test_edit_requires_csrf_and_get_does_not_write(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(reverse('locations:edit', args=[self.center.pk]), self.payload()).status_code, 403)
        self.client.force_login(self.admin)
        self.client.get(reverse('locations:edit', args=[self.center.pk]))
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_navigation_and_search(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('locations:manage_list'), {'q': 'prueba'})
        self.assertContains(response, f'href="{reverse("locations:manage_list")}"')
        self.assertContains(response, self.center.name)
        self.assertEqual(list(response.context['centers']), [self.center])

    def test_seed_reuses_demo_ids_is_idempotent_and_preserves_custom_data(self):
        legacy = RecyclingCenter.objects.create(name='Punto Limpio de Santa Cruz', slug='punto-limpio-santa-cruz', address='Tenerife', schedule='Lunes a sabado')
        lagoon = RecyclingCenter.objects.create(name='Punto Limpio de La Laguna', slug='punto-limpio-la-laguna', address='Tenerife')
        guimar = RecyclingCenter.objects.create(name='Punto Limpio de Guimar', slug='punto-limpio-güimar', address='Tenerife')
        output = StringIO()
        call_command('seed_public_centers', publish=True, stdout=output)
        legacy.refresh_from_db()
        lagoon.refresh_from_db()
        guimar.refresh_from_db()
        self.assertEqual(legacy.name, 'Punto Limpio de Jagua')
        self.assertEqual(lagoon.name, 'Punto Limpio de Taco')
        self.assertEqual(guimar.name, 'Punto Limpio de Güímar')
        self.assertEqual(guimar.slug, 'punto-limpio-güimar')
        self.assertEqual(legacy.latitude, Decimal('28.500707'))
        legacy.schedule = 'Horario personalizado'
        legacy.phone = '922 000 000'
        legacy.save()
        count = RecyclingCenter.objects.count()
        audit_count = AuditEvent.objects.count()
        call_command('seed_public_centers', publish=True, stdout=output)
        self.assertEqual(RecyclingCenter.objects.count(), count)
        self.assertEqual(AuditEvent.objects.count(), audit_count)
        legacy.refresh_from_db()
        self.assertEqual(legacy.schedule, 'Horario personalizado')
        self.assertEqual(legacy.phone, '922 000 000')
        self.assertEqual(RecyclingCenter.objects.filter(show_on_public_map=True).count(), 9)

    def test_seed_does_not_relabel_a_configured_santa_cruz_center(self):
        configured = RecyclingCenter.objects.create(name='Centro personalizado', slug='punto-limpio-santa-cruz', address='Otra dirección', latitude='28.4', longitude='-16.3')
        call_command('seed_public_centers', stdout=StringIO())
        configured.refresh_from_db()
        self.assertEqual(configured.name, 'Centro personalizado')
        self.assertTrue(RecyclingCenter.objects.filter(slug='punto-limpio-jagua').exists())
