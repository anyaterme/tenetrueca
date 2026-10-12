from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from accounts.models import UserCenterAccess
from core.roles import ROLE_STAFF_ADMIN, ROLE_STAFF_MANAGER
from faq.forms import FAQForm
from faq.models import FAQ
from faq.permissions import user_can_manage_faq
from locations.models import RecyclingCenter
from audit.models import AuditEvent


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class FAQTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin_group = Group.objects.get(name=ROLE_STAFF_ADMIN)
        cls.manager_group = Group.objects.get(name=ROLE_STAFF_MANAGER)
        cls.admin = User.objects.create_user(
            email='faq-admin@example.com', first_name='Ada', is_staff=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.admin.groups.add(cls.admin_group)
        cls.manager = User.objects.create_user(
            email='faq-manager@example.com', first_name='Marta', is_staff=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.manager.groups.add(cls.manager_group)
        cls.center = RecyclingCenter.objects.create(name='Centro FAQ', slug='centro-faq')
        UserCenterAccess.objects.create(
            user=cls.manager, center=cls.center, role=UserCenterAccess.ScopeRole.OPERATOR,
        )
        cls.citizen = User.objects.create_user(
            email='faq-citizen@example.com', first_name='Celia',
            account_status=User.AccountStatus.ACTIVE,
        )
        cls.staff_without_permissions = User.objects.create_user(
            email='faq-staff@example.com', first_name='Sam', is_staff=True,
            account_status=User.AccountStatus.ACTIVE,
        )

    def payload(self, **overrides):
        return {
            'question': '¿Cómo puedo intercambiar?', 'answer': 'Publica tu objeto.',
            'position': 3, 'is_published': 'on', **overrides,
        }

    def faq(self, **overrides):
        return FAQ.objects.create(**{
            'question': '¿Cómo funciona?', 'answer': 'Intercambia tus objetos.',
            **overrides,
        })

    def test_public_access_without_login_and_footer_link(self):
        response = self.client.get(reverse('faq:public_list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'layouts/public.html')
        self.assertContains(self.client.get(reverse('home')), 'href="/faq/"')

    def test_public_only_published_even_for_staff(self):
        public = self.faq(question='Pregunta pública')
        self.faq(question='Borrador secreto', answer='Respuesta secreta', is_published=False)
        for user in (None, self.admin):
            if user:
                self.client.force_login(user)
            response = self.client.get(reverse('faq:public_list'))
            self.assertEqual(list(response.context['faqs']), [public])
            self.assertNotContains(response, 'Borrador secreto')
            self.assertNotContains(response, 'Respuesta secreta')

    def test_order_by_position_then_id(self):
        last = self.faq(position=8)
        first = self.faq(position=1)
        second = self.faq(position=1)
        response = self.client.get(reverse('faq:public_list'))
        self.assertEqual(list(response.context['faqs']), [first, second, last])
        self.client.force_login(self.admin)
        response = self.client.get(reverse('faq:manage_list'))
        self.assertEqual(list(response.context['faqs']), [first, second, last])

    def test_empty_public_state_includes_only_unpublished_case(self):
        for create_draft in (False, True):
            if create_draft:
                self.faq(is_published=False)
            response = self.client.get(reverse('faq:public_list'))
            self.assertContains(response, 'Todavía no hay preguntas frecuentes')
            self.assertNotContains(response, 'data-faq-input')

    def test_search_and_native_accordion_markup(self):
        faq = self.faq()
        response = self.client.get(reverse('faq:public_list'))
        self.assertContains(response, '<details class="faq-item" data-faq-item>')
        self.assertContains(response, f'aria-labelledby="faq-question-{faq.pk}"')
        self.assertContains(response, 'type="search"')
        self.assertContains(response, 'data-faq-no-results')
        self.assertContains(response, 'aria-live="polite"')

    def test_question_and_answer_are_escaped(self):
        self.faq(question='<img src=x onerror=alert(1)>', answer='<script>alert(1)</script>\nTexto')
        response = self.client.get(reverse('faq:public_list'))
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;<br>Texto')
        self.assertContains(response, '&lt;img src=x onerror=alert(1)&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse('faq:manage_list')), '<img src=x onerror=alert(1)>')

    def test_admin_and_manager_can_create(self):
        for user in (self.admin, self.manager):
            with self.subTest(user=user.email):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse('faq:create')).status_code, 200)
                response = self.client.post(reverse('faq:create'), self.payload())
                self.assertRedirects(response, reverse('faq:manage_list'))
        self.assertEqual(FAQ.objects.count(), 2)
        self.assertTrue(FAQ.objects.first().is_published)

    def test_admin_and_manager_can_edit_publication_and_position(self):
        faq = self.faq()
        for user in (self.admin, self.manager):
            with self.subTest(user=user.email):
                self.client.force_login(user)
                response = self.client.post(reverse('faq:edit', args=[faq.pk]), self.payload(
                    question='Pregunta actualizada', answer='Nueva respuesta', position=7, is_published='',
                ))
                self.assertRedirects(response, reverse('faq:manage_list'))
                faq.refresh_from_db()
                self.assertEqual((faq.question, faq.answer, faq.position, faq.is_published),
                                 ('Pregunta actualizada', 'Nueva respuesta', 7, False))
                self.assertNotContains(self.client.get(reverse('faq:public_list')), 'Pregunta actualizada')
                self.client.post(reverse('faq:edit', args=[faq.pk]), self.payload())
                faq.refresh_from_db()
                self.assertTrue(faq.is_published)

    def test_admin_and_manager_can_delete_after_confirmation(self):
        for user in (self.admin, self.manager):
            with self.subTest(user=user.email):
                self.client.force_login(user)
                faq = self.faq()
                response = self.client.get(reverse('faq:delete_confirm', args=[faq.pk]))
                self.assertContains(response, 'Confirmo que quiero eliminar esta pregunta.')
                self.assertTrue(FAQ.objects.filter(pk=faq.pk).exists())
                response = self.client.post(reverse('faq:delete', args=[faq.pk]), {'confirm': 'on'})
                self.assertRedirects(response, reverse('faq:manage_list'))
                self.assertFalse(FAQ.objects.filter(pk=faq.pk).exists())

    def test_delete_requires_post_and_explicit_confirmation(self):
        self.client.force_login(self.admin)
        faq = self.faq()
        self.assertEqual(self.client.get(reverse('faq:delete', args=[faq.pk])).status_code, 405)
        self.assertEqual(self.client.post(reverse('faq:delete', args=[faq.pk])).status_code, 400)
        self.assertEqual(self.client.post(reverse('faq:delete_confirm', args=[faq.pk])).status_code, 405)
        self.assertTrue(FAQ.objects.filter(pk=faq.pk).exists())

    def test_admin_list_includes_drafts_and_empty_call_to_action(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('faq:manage_list'))
        self.assertContains(response, 'Todavía no hay preguntas frecuentes')
        self.assertContains(response, 'Nueva pregunta')
        self.faq(question='Borrador', is_published=False)
        self.faq(question='Visible')
        response = self.client.get(reverse('faq:manage_list'))
        self.assertContains(response, 'Borrador')
        self.assertContains(response, 'No publicada')
        self.assertContains(response, 'Publicada')

    def test_unauthorized_users_cannot_access_or_write(self):
        faq = self.faq()
        routes = [
            reverse('faq:manage_list'), reverse('faq:create'),
            reverse('faq:edit', args=[faq.pk]), reverse('faq:delete_confirm', args=[faq.pk]),
            reverse('faq:delete', args=[faq.pk]),
        ]
        for user in (None, self.citizen, self.staff_without_permissions):
            self.client.logout()
            if user:
                self.client.force_login(user)
            for url in routes:
                for method in ('get', 'post'):
                    with self.subTest(user=user, url=url, method=method):
                        response = getattr(self.client, method)(url, self.payload(confirm='on'))
                        self.assertEqual(response.status_code, 403 if user else 302)
        faq.refresh_from_db()
        self.assertEqual(faq.question, '¿Cómo funciona?')
        self.assertEqual(FAQ.objects.count(), 1)

    def test_view_permission_does_not_grant_write_permissions(self):
        user = self.staff_without_permissions
        user.user_permissions.add(Permission.objects.get(content_type__app_label='faq', codename='view_faq'))
        self.client.force_login(user)
        faq = self.faq()
        response = self.client.get(reverse('faq:manage_list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Nueva pregunta')
        for name in ('create', 'edit', 'delete'):
            url = reverse(f'faq:{name}', args=[] if name == 'create' else [faq.pk])
            self.assertEqual(self.client.post(url, self.payload(confirm='on')).status_code, 403)

    def test_permission_policy_for_inactive_users_and_nonstaff(self):
        self.assertFalse(user_can_manage_faq(AnonymousUser()))
        user = get_user_model().objects.get(pk=self.admin.pk)
        user.is_active = False
        self.assertFalse(user_can_manage_faq(user, 'add'))
        self.citizen.user_permissions.add(*Permission.objects.filter(content_type__app_label='faq'))
        self.assertFalse(user_can_manage_faq(self.citizen, 'add'))
        self.client.force_login(self.citizen)
        self.assertEqual(self.client.post(reverse('faq:create'), self.payload()).status_code, 403)

    def test_revoked_role_permission_is_enforced(self):
        self.admin_group.permissions.remove(Permission.objects.get(content_type__app_label='faq', codename='add_faq'))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('faq:create'), self.payload()).status_code, 403)

    def test_roles_receive_only_faq_permissions(self):
        expected = {'view_faq', 'add_faq', 'change_faq', 'delete_faq'}
        for group in (self.admin_group, self.manager_group):
            self.assertEqual(set(group.permissions.filter(content_type__app_label='faq').values_list('codename', flat=True)), expected)
        self.assertFalse(self.manager_group.permissions.filter(content_type__app_label='accounts', codename='manage_staff').exists())

    def test_manager_without_center_keeps_existing_restriction(self):
        UserCenterAccess.objects.filter(user=self.manager).delete()
        self.client.force_login(self.manager)
        self.assertRedirects(self.client.get(reverse('faq:manage_list')), reverse('backoffice:no_assignment'))
        self.assertRedirects(self.client.post(reverse('faq:create'), self.payload()), reverse('backoffice:no_assignment'))
        self.assertEqual(FAQ.objects.count(), 0)

    def test_required_fields_length_and_nonnegative_position(self):
        self.client.force_login(self.admin)
        invalid = [
            {'question': ''}, {'question': '   '}, {'answer': ''}, {'answer': '   '},
            {'question': 'x' * 256}, {'position': -1}, {'position': ''}, {'position': 'no'},
        ]
        for values in invalid:
            with self.subTest(values=values):
                response = self.client.post(reverse('faq:create'), self.payload(**values))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.assertContains(response, 'Revisa los campos indicados')
        self.assertEqual(FAQ.objects.count(), 0)
        form = FAQForm(self.payload(position=0))
        self.assertTrue(form.is_valid(), form.errors)

    def test_csrf_is_required_for_every_write(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        faq = self.faq()
        for url in (reverse('faq:create'), reverse('faq:edit', args=[faq.pk]), reverse('faq:delete', args=[faq.pk])):
            self.assertEqual(client.post(url, self.payload(confirm='on')).status_code, 403)
        client.get(reverse('faq:create'))
        token = client.cookies['csrftoken'].value
        response = client.post(reverse('faq:create'), self.payload(csrfmiddlewaretoken=token))
        self.assertRedirects(response, reverse('faq:manage_list'))
        response = client.post(reverse('faq:edit', args=[faq.pk]), self.payload(csrfmiddlewaretoken=token))
        self.assertRedirects(response, reverse('faq:manage_list'))
        response = client.post(reverse('faq:delete', args=[faq.pk]), {'confirm': 'on', 'csrfmiddlewaretoken': token})
        self.assertRedirects(response, reverse('faq:manage_list'))

    def test_missing_records_return_404(self):
        self.client.force_login(self.admin)
        for name in ('edit', 'delete_confirm'):
            self.assertEqual(self.client.get(reverse(f'faq:{name}', args=[999999])).status_code, 404)
        self.assertEqual(self.client.post(reverse('faq:delete', args=[999999]), {'confirm': 'on'}).status_code, 404)

    def test_timestamps_defaults_and_string(self):
        faq = self.faq()
        self.assertTrue(faq.is_published)
        self.assertEqual(faq.position, 0)
        self.assertEqual(str(faq), faq.question)
        created_at, updated_at = faq.created_at, faq.updated_at
        faq.answer = 'Nueva respuesta'
        faq.save()
        faq.refresh_from_db()
        self.assertEqual(faq.created_at, created_at)
        self.assertGreater(faq.updated_at, updated_at)

    def test_creation_is_audited_with_actor_and_full_snapshot(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('faq:create'), self.payload())
        faq = FAQ.objects.get()
        event = AuditEvent.objects.get(entity='FAQ')
        self.assertEqual(event.action, 'faq.created')
        self.assertEqual(event.entity_id, str(faq.pk))
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.metadata['actor_label'], 'Ada')
        self.assertIsNone(event.before)
        self.assertEqual(event.after, {'question': faq.question, 'answer': faq.answer, 'position': 3, 'is_published': True})
        self.assertIsNotNone(event.created_at)

    def test_edit_records_changed_content_position_and_publication(self):
        self.client.force_login(self.manager)
        faq = self.faq()
        self.client.post(reverse('faq:edit', args=[faq.pk]), self.payload(question='Pregunta nueva', answer='Respuesta nueva', position=8, is_published=''))
        event = AuditEvent.objects.get(entity='FAQ')
        self.assertEqual(event.action, 'faq.updated')
        self.assertEqual(event.actor, self.manager)
        self.assertEqual(event.before, {'question': '¿Cómo funciona?', 'answer': 'Intercambia tus objetos.', 'position': 0, 'is_published': True})
        self.assertEqual(event.after, {'question': 'Pregunta nueva', 'answer': 'Respuesta nueva', 'position': 8, 'is_published': False})
        response = self.client.get(reverse('faq:question_history', args=[faq.pk]))
        self.assertContains(response, 'Marta')
        self.assertContains(response, 'Pregunta nueva')
        self.assertContains(response, '¿Cómo funciona?')
        self.assertEqual(len(response.context['history_items'][0]['changes']), 4)

    def test_unchanged_invalid_and_get_requests_do_not_create_history(self):
        self.client.force_login(self.admin)
        faq = self.faq()
        updated_at = faq.updated_at
        self.client.get(reverse('faq:edit', args=[faq.pk]))
        self.client.post(reverse('faq:create'), self.payload(question=''))
        self.client.post(reverse('faq:edit', args=[faq.pk]), self.payload(question=faq.question, answer=faq.answer, position=faq.position))
        self.client.post(reverse('faq:delete', args=[faq.pk]), {})
        self.assertFalse(AuditEvent.objects.filter(entity='FAQ').exists())
        faq.refresh_from_db()
        self.assertEqual(faq.updated_at, updated_at)

    def test_deletion_retains_snapshot_and_history_after_actor_removal(self):
        self.client.force_login(self.admin)
        faq = self.faq()
        faq_id = faq.pk
        self.client.post(reverse('faq:delete', args=[faq_id]), {'confirm': 'on'})
        event = AuditEvent.objects.get(entity='FAQ')
        self.assertEqual(event.action, 'faq.deleted')
        self.assertIsNone(event.after)
        self.assertEqual(event.before['question'], faq.question)
        self.assertFalse(FAQ.objects.filter(pk=faq_id).exists())
        self.admin.delete()
        self.client.force_login(self.manager)
        response = self.client.get(reverse('faq:question_history', args=[faq_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Eliminación')
        self.assertContains(response, faq.question)
        self.assertContains(response, 'Ada')
        event.refresh_from_db()
        self.assertIsNone(event.actor)

    def test_audit_failure_rolls_back_creates_edits_and_deletions(self):
        self.client.force_login(self.admin)
        faq = self.faq()
        original_answer = faq.answer
        requests = (
            (reverse('faq:create'), self.payload()),
            (reverse('faq:edit', args=[faq.pk]), self.payload(answer='No debe guardarse')),
            (reverse('faq:delete', args=[faq.pk]), {'confirm': 'on'}),
        )
        for url, payload in requests:
            with self.subTest(url=url), patch('faq.audit.AuditEvent.objects.create', side_effect=RuntimeError('Audit unavailable')):
                with self.assertRaises(RuntimeError):
                    self.client.post(url, payload)
            self.assertEqual(FAQ.objects.count(), 1)
            faq.refresh_from_db()
            self.assertEqual(faq.answer, original_answer)
        self.assertFalse(AuditEvent.objects.filter(entity='FAQ').exists())

    def test_history_permissions_filtering_pagination_and_html_escaping(self):
        from faq.audit import record_faq_event
        faq = self.faq(question='<script>alert(1)</script>')
        second = self.faq(question='Otra pregunta')
        for index in range(21):
            record_faq_event(actor=self.admin, faq_id=faq.pk, action='faq.updated',
                             before={'question': 'Anterior', 'answer': str(index)},
                             after={'question': faq.question, 'answer': str(index + 1)})
        record_faq_event(actor=self.admin, faq_id=second.pk, action='faq.created', after={'question': second.question})
        url = reverse('faq:history')
        self.assertEqual(self.client.get(url).status_code, 302)
        for user in (self.citizen, self.staff_without_permissions):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.get(reverse('faq:question_history', args=[faq.pk])).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(url, {'action': 'faq.updated'})
        self.assertEqual(response.context['paginator'].count, 21)
        self.assertEqual(len(response.context['history_items']), 20)
        self.assertContains(response, 'action=faq.updated&amp;page=2')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        response = self.client.get(url, {'action': 'faq.updated', 'page': 2})
        self.assertEqual(len(response.context['history_items']), 1)
        response = self.client.get(reverse('faq:question_history', args=[faq.pk]))
        self.assertNotContains(response, second.question)
        self.assertEqual(response.context['paginator'].count, 21)
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertNotContains(self.client.get(reverse('faq:public_list')), 'faq-history')

    def test_django_admin_changes_and_bulk_deletions_are_audited(self):
        from django.contrib import admin
        from django.test import RequestFactory
        from faq.admin import FAQAdmin
        model_admin = FAQAdmin(FAQ, admin.site)
        request = RequestFactory().post('/admin/faq/faq/')
        request.user = self.admin
        faq = FAQ(question='Creación desde admin', answer='Respuesta')
        model_admin.save_model(request, faq, None, change=False)
        self.assertEqual(AuditEvent.objects.get(entity='FAQ').action, 'faq.created')
        faq.answer = 'Actualización desde admin'
        model_admin.save_model(request, faq, None, change=True)
        event = AuditEvent.objects.filter(action='faq.updated').get()
        self.assertEqual(event.before['answer'], 'Respuesta')
        self.assertEqual(event.after['answer'], faq.answer)
        self.assertEqual(event.source, 'django_admin')
        second = self.faq()
        ids = {str(faq.pk), str(second.pk)}
        model_admin.delete_queryset(request, FAQ.objects.filter(pk__in=ids))
        self.assertEqual(set(AuditEvent.objects.filter(action='faq.deleted').values_list('entity_id', flat=True)), ids)
        self.assertEqual(FAQ.objects.count(), 0)
        third = self.faq()
        model_admin.delete_model(request, third)
        self.assertEqual(AuditEvent.objects.filter(action='faq.deleted').count(), 3)

    def test_history_navigation_is_available_for_existing_questions_without_events(self):
        self.client.force_login(self.admin)
        faq = self.faq()
        history_url = reverse('faq:question_history', args=[faq.pk])
        self.assertContains(self.client.get(reverse('faq:manage_list')), f'href="{history_url}"')
        self.assertContains(self.client.get(reverse('faq:edit', args=[faq.pk])), 'Ver historial')
        self.assertContains(self.client.get(history_url), 'No hay actividad registrada')
