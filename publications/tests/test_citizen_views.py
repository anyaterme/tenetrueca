from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from audit.models import AuditEvent
from catalog.models import Category
from configuration.models import FunctionalRule
from inventory.models import InventoryItem
from moderation.models import ModerationDecision
from publications.models import Publication, PublicationPhoto


def image_upload(name='object.jpg', image_format='JPEG'):
    image = Image.new('RGB', (48, 36), '#1673b8')
    payload = BytesIO()
    image.save(payload, format=image_format)
    return SimpleUploadedFile(name, payload.getvalue(), content_type='application/octet-stream')


class CitizenPublicationViewsTests(TestCase):
    def setUp(self):
        self.private_media = TemporaryDirectory()
        self.settings_override = override_settings(PRIVATE_MEDIA_ROOT=self.private_media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.private_media.cleanup)

        User = get_user_model()
        self.user = User.objects.create_user(
            email='citizen@example.com',
            username='citizen',
            first_name='Citizen',
            password='test-password',
        )
        self.other_user = User.objects.create_user(
            email='other@example.com',
            username='other',
            first_name='Other',
            password='test-password',
        )
        self.reviewer = User.objects.create_user(
            email='reviewer@example.com',
            username='reviewer',
            first_name='Reviewer',
            password='test-password',
        )
        self.parent_category = Category.objects.create(name='Hogar', slug='hogar')
        self.category = Category.objects.create(
            name='Iluminación',
            slug='iluminacion',
            parent=self.parent_category,
        )

    def publication_data(self, **overrides):
        data = {
            'title': 'Lámpara ciudadana',
            'description': 'Lámpara en buen estado y plenamente funcional.',
            'parent_category': str(self.parent_category.pk),
            'category': str(self.category.pk),
            'action': 'save_draft',
        }
        data.update(overrides)
        return data

    def create_publication(self, user=None, status=Publication.Status.DRAFT, title='Propuesta'):
        return Publication.objects.create(
            submitter=user or self.user,
            title=title,
            description='Descripción de la propuesta.',
            category=self.category,
            status=status,
            submitted_at=timezone.now() if status != Publication.Status.DRAFT else None,
        )

    def test_authentication_is_required_for_panel_and_creation(self):
        for url in (reverse('publications:list'), reverse('publications:create')):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)

    def test_draft_uses_session_user_and_does_not_create_inventory(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('publications:create'),
            self.publication_data(submitter=str(self.other_user.pk)),
        )

        publication = Publication.objects.get(title='Lámpara ciudadana')
        self.assertRedirects(response, reverse('publications:detail', args=[publication.pk]))
        self.assertEqual(publication.submitter, self.user)
        self.assertEqual(publication.status, Publication.Status.DRAFT)
        self.assertIsNone(publication.submitted_at)
        self.assertFalse(InventoryItem.objects.filter(publication=publication).exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                actor=self.user,
                action='publication.draft_created',
                entity_id=str(publication.pk),
            ).exists()
        )

    def test_submit_normalizes_private_photo_without_publishing_inventory(self):
        self.client.force_login(self.user)
        data = self.publication_data(action='submit')
        data['photos'] = image_upload()

        response = self.client.post(reverse('publications:create'), data)

        publication = Publication.objects.get(title='Lámpara ciudadana')
        photo = publication.photos.get()
        self.assertRedirects(response, reverse('publications:detail', args=[publication.pk]))
        self.assertEqual(publication.status, Publication.Status.PENDING_REVIEW)
        self.assertIsNotNone(publication.submitted_at)
        self.assertEqual(photo.image_format, 'JPEG')
        self.assertNotIn('object.jpg', photo.image.name)
        self.assertFalse(InventoryItem.objects.filter(publication=publication).exists())
        self.assertEqual(InventoryItem.objects.public_catalog().count(), 0)

        photo_response = self.client.get(reverse('publication_photo', args=[photo.pk]))
        self.assertEqual(photo_response.status_code, 200)
        self.assertEqual(photo_response['Cache-Control'], 'private, no-store')

    def test_publication_wizard_exposes_camera_gallery_and_four_steps(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('publications:create'))

        self.assertContains(response, 'data-publication-editor')
        self.assertContains(response, 'capture="environment"')
        self.assertContains(response, 'multiple')
        self.assertContains(response, 'Tomar foto')
        self.assertContains(response, 'Elegir de galería')
        for step in ('Fotos', 'Información', 'Categoría', 'Revisar'):
            self.assertContains(response, step)

    def test_category_tree_is_generated_from_active_database_categories(self):
        inactive = Category.objects.create(name='Oculta', slug='oculta', is_active=False)
        self.client.force_login(self.user)

        response = self.client.get(reverse('publications:create'))

        category_ids = {category['id'] for category in response.context['publication_categories']}
        self.assertIn(self.parent_category.pk, category_ids)
        self.assertIn(self.category.pk, category_ids)
        self.assertNotIn(inactive.pk, category_ids)

    def test_second_new_photo_can_be_selected_as_primary(self):
        self.client.force_login(self.user)
        data = self.publication_data(action='submit', primary_photo='new:1')
        data['photos'] = [image_upload('one.jpg'), image_upload('two.jpg')]

        response = self.client.post(reverse('publications:create'), data)

        publication = Publication.objects.get(title='Lámpara ciudadana')
        photos = list(publication.photos.order_by('sort_order'))
        self.assertRedirects(response, reverse('publications:detail', args=[publication.pk]))
        self.assertFalse(photos[0].is_primary)
        self.assertTrue(photos[1].is_primary)

    def test_existing_photo_can_be_changed_to_primary(self):
        publication = self.create_publication(title='Borrador con fotos')
        first = PublicationPhoto.objects.create(
            publication=publication,
            image=image_upload('first.jpg'),
            sort_order=0,
            is_primary=True,
        )
        second = PublicationPhoto.objects.create(
            publication=publication,
            image=image_upload('second.jpg'),
            sort_order=1,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('publications:edit', args=[publication.pk]),
            self.publication_data(
                title=publication.title,
                primary_photo=f'existing:{second.pk}',
            ),
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertRedirects(response, reverse('publications:detail', args=[publication.pk]))
        self.assertFalse(first.is_primary)
        self.assertTrue(second.is_primary)

    def test_removing_primary_photo_promotes_first_remaining_photo(self):
        publication = self.create_publication(title='Borrador para limpiar')
        first = PublicationPhoto.objects.create(
            publication=publication,
            image=image_upload('first.jpg'),
            sort_order=0,
            is_primary=True,
        )
        second = PublicationPhoto.objects.create(
            publication=publication,
            image=image_upload('second.jpg'),
            sort_order=1,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('publications:edit', args=[publication.pk]),
            self.publication_data(
                title=publication.title,
                remove_photos=[str(first.pk)],
                primary_photo=f'existing:{first.pk}',
            ),
        )

        second.refresh_from_db()
        self.assertRedirects(response, reverse('publications:detail', args=[publication.pk]))
        self.assertFalse(PublicationPhoto.objects.filter(pk=first.pk).exists())
        self.assertTrue(second.is_primary)

    def test_primary_photo_cannot_reference_another_users_publication(self):
        publication = self.create_publication(title='Borrador propio')
        other_publication = self.create_publication(
            user=self.other_user,
            title='Borrador ajeno con foto',
        )
        foreign_photo = PublicationPhoto.objects.create(
            publication=other_publication,
            image=image_upload('foreign.jpg'),
            is_primary=True,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('publications:edit', args=[publication.pk]),
            self.publication_data(
                title=publication.title,
                primary_photo=f'existing:{foreign_photo.pk}',
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Selecciona una fotografía principal válida')

    def test_edit_is_limited_by_owner_and_status(self):
        draft = self.create_publication(title='Borrador editable')
        changes = self.create_publication(
            status=Publication.Status.CHANGES_REQUESTED,
            title='Cambios editables',
        )
        pending = self.create_publication(
            status=Publication.Status.PENDING_REVIEW,
            title='Pendiente bloqueada',
        )
        other = self.create_publication(user=self.other_user, title='Publicación ajena')
        self.client.force_login(self.user)

        self.assertEqual(
            self.client.get(reverse('publications:edit', args=[draft.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse('publications:edit', args=[changes.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse('publications:edit', args=[pending.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse('publications:edit', args=[other.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse('publications:detail', args=[other.pk])).status_code,
            404,
        )

    def test_changes_requested_can_be_resubmitted(self):
        publication = self.create_publication(
            status=Publication.Status.CHANGES_REQUESTED,
            title='Objeto a corregir',
        )
        PublicationPhoto.objects.create(
            publication=publication,
            image=image_upload(),
            is_primary=True,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('publications:edit', args=[publication.pk]),
            self.publication_data(title='Objeto corregido', action='submit'),
        )

        publication.refresh_from_db()
        self.assertRedirects(response, reverse('publications:detail', args=[publication.pk]))
        self.assertEqual(publication.title, 'Objeto corregido')
        self.assertEqual(publication.status, Publication.Status.PENDING_REVIEW)
        self.assertIsNotNone(publication.submitted_at)

    def test_withdrawal_requires_owner_allowed_status_and_reason(self):
        publication = self.create_publication(status=Publication.Status.PENDING_REVIEW)
        other = self.create_publication(user=self.other_user, title='Otra propuesta')
        approved = self.create_publication(status=Publication.Status.APPROVED, title='Aprobada')
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('publications:withdraw', args=[publication.pk]),
            {'reason': 'Ya no deseo entregar el objeto.'},
        )

        publication.refresh_from_db()
        self.assertRedirects(response, reverse('publications:detail', args=[publication.pk]))
        self.assertEqual(publication.status, Publication.Status.WITHDRAWN)
        event = AuditEvent.objects.get(action='publication.withdrawn', entity_id=str(publication.pk))
        self.assertEqual(event.metadata['reason'], 'Ya no deseo entregar el objeto.')
        self.assertEqual(
            self.client.get(reverse('publications:withdraw', args=[other.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse('publications:withdraw', args=[approved.pk])).status_code,
            403,
        )

    def test_daily_limit_counts_submissions_but_allows_drafts(self):
        FunctionalRule.objects.create(
            key='limite-publicaciones-diarias',
            name='Límite diario',
            value_type=FunctionalRule.ValueType.INTEGER,
            value=1,
        )
        self.create_publication(status=Publication.Status.PENDING_REVIEW, title='Enviada hoy')
        self.client.force_login(self.user)

        rejected_response = self.client.post(
            reverse('publications:create'),
            {
                **self.publication_data(action='submit'),
                'photos': image_upload(),
            },
        )
        self.assertEqual(rejected_response.status_code, 200)
        self.assertContains(rejected_response, 'Has alcanzado el límite de 1 publicaciones')
        self.assertFalse(Publication.objects.filter(title='Lámpara ciudadana').exists())

        draft_response = self.client.post(
            reverse('publications:create'),
            self.publication_data(action='save_draft'),
        )
        self.assertEqual(draft_response.status_code, 302)
        self.assertTrue(
            Publication.objects.filter(
                title='Lámpara ciudadana',
                status=Publication.Status.DRAFT,
            ).exists()
        )

    def test_invalid_and_excess_photos_are_rejected(self):
        self.client.force_login(self.user)
        invalid_data = self.publication_data(action='submit')
        invalid_data['photos'] = SimpleUploadedFile(
            'fake.jpg',
            b'not an image',
            content_type='image/jpeg',
        )

        invalid_response = self.client.post(reverse('publications:create'), invalid_data)

        self.assertEqual(invalid_response.status_code, 200)
        self.assertContains(invalid_response, 'imagen válida o está corrupto')
        self.assertEqual(Publication.objects.count(), 0)

        with override_settings(PUBLICATION_PHOTO_MAX_COUNT=1):
            excess_data = self.publication_data(action='submit')
            excess_data['photos'] = [image_upload('one.jpg'), image_upload('two.jpg')]
            excess_response = self.client.post(reverse('publications:create'), excess_data)
        self.assertEqual(excess_response.status_code, 200)
        self.assertContains(excess_response, 'máximo de 1 fotografías')
        self.assertEqual(Publication.objects.count(), 0)

    @override_settings(PUBLICATION_PAGE_SIZE=1)
    def test_panel_filters_paginates_and_shows_review_feedback(self):
        draft = self.create_publication(title='Borrador propio')
        changes = self.create_publication(
            status=Publication.Status.CHANGES_REQUESTED,
            title='Cambios propios',
        )
        self.create_publication(user=self.other_user, title='Borrador ajeno')
        ModerationDecision.objects.create(
            publication=changes,
            reviewer=self.reviewer,
            decision=ModerationDecision.Decision.REQUEST_CHANGES,
            previous_status=Publication.Status.PENDING_REVIEW,
            resulting_status=Publication.Status.CHANGES_REQUESTED,
            notes='Añade una fotografía más clara del objeto.',
        )
        self.client.force_login(self.user)

        first_page = self.client.get(reverse('publications:list'))
        self.assertEqual(first_page.context['paginator'].count, 2)
        self.assertNotContains(first_page, 'Borrador ajeno')
        self.assertContains(first_page, 'page=2')

        filtered = self.client.get(
            reverse('publications:list'),
            {'status': Publication.Status.CHANGES_REQUESTED},
        )
        self.assertContains(filtered, changes.title)
        self.assertContains(filtered, 'Añade una fotografía más clara del objeto.')
        self.assertNotContains(filtered, draft.title)

        second_page = self.client.get(reverse('publications:list'), {'page': 2})
        self.assertEqual(len(second_page.context['page_obj'].object_list), 1)
