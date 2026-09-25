from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from catalog.models import Category, ReusableObject
from inventory.models import InventoryItem
from locations.models import RecyclingCenter
from publications.models import Publication, PublicationPhoto
from reservations.models import Reservation


def photo_upload(name='catalog.jpg'):
    image = Image.new('RGB', (48, 36), '#1673b8')
    payload = BytesIO()
    image.save(payload, format='JPEG')
    return SimpleUploadedFile(name, payload.getvalue(), content_type='image/jpeg')


class PublicCatalogTests(TestCase):
    def setUp(self):
        self.private_media = TemporaryDirectory()
        self.settings_override = override_settings(PRIVATE_MEDIA_ROOT=self.private_media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.private_media.cleanup)

        User = get_user_model()
        self.owner = User.objects.create_user(
            email='catalog-owner@example.com',
            username='catalog-owner',
            first_name='Owner',
            password='test-password',
        )
        self.validator = User.objects.create_user(
            email='catalog-validator@example.com',
            username='catalog-validator',
            first_name='Validator',
            password='test-password',
        )
        self.requester = User.objects.create_user(
            email='catalog-requester@example.com',
            username='catalog-requester',
            first_name='Requester',
            password='test-password',
        )
        self.parent_category = Category.objects.create(name='Hogar', slug='hogar')
        self.category = Category.objects.create(
            name='Muebles',
            slug='muebles',
            parent=self.parent_category,
        )
        self.other_category = Category.objects.create(name='Deporte', slug='deporte')
        self.center = RecyclingCenter.objects.create(
            name='Punto Limpio Norte',
            slug='punto-limpio-norte',
            public_information='Recogida con cita previa.',
        )
        self.other_center = RecyclingCenter.objects.create(
            name='Punto Limpio Sur',
            slug='punto-limpio-sur',
        )
        self.sequence = 0

    def create_item(
        self,
        title,
        *,
        category=None,
        center=None,
        item_status=ReusableObject.Status.AVAILABLE,
        publication_status=Publication.Status.APPROVED,
        description='Descripción pública del objeto.',
        with_photo=False,
    ):
        self.sequence += 1
        category = category or self.category
        center = center or self.center
        publication = Publication.objects.create(
            submitter=self.owner,
            title=title,
            description=description,
            category=category,
            status=Publication.Status.APPROVED,
            approved_at=timezone.now() - timedelta(minutes=self.sequence),
        )
        item = InventoryItem.objects.create(
            reference=f'CAT-{self.sequence:04d}',
            owner=self.owner,
            publication=publication,
            center=center,
            title=title,
            description=description,
            category=category.name,
            category_node=category,
            internal_location=f'PRIVATE-{self.sequence}',
            condition=ReusableObject.Condition.GOOD,
            status=item_status,
            validated_at=timezone.now(),
            validated_by=self.validator,
        )
        if publication_status != Publication.Status.APPROVED:
            publication.status = publication_status
            publication.save()
        if with_photo:
            PublicationPhoto.objects.create(
                publication=publication,
                image=photo_upload(),
                alt_text=f'Fotografía de {title}',
                is_primary=True,
            )
        return item

    def test_list_only_shows_published_available_objects(self):
        visible = self.create_item('Objeto visible')
        self.create_item('Objeto entregado', item_status=ReusableObject.Status.DELIVERED)
        self.create_item('Publicación pendiente', publication_status=Publication.Status.PENDING_REVIEW)
        self.create_item('Publicación rechazada', publication_status=Publication.Status.REJECTED)
        self.create_item('Publicación retirada', publication_status=Publication.Status.WITHDRAWN)
        reserved = self.create_item('Objeto con reserva activa')
        Reservation.objects.create(
            inventory_item=reserved,
            user=self.requester,
            expires_at=timezone.now() + timedelta(days=1),
        )
        ReusableObject.objects.create(
            reference='LEGACY-AVAILABLE',
            owner=self.owner,
            center=self.center,
            title='Objeto sin publicación',
            category='Hogar',
            condition=ReusableObject.Condition.GOOD,
            status=ReusableObject.Status.AVAILABLE,
        )

        response = self.client.get(reverse('catalog:list'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['page_obj'].object_list), [visible])
        self.assertContains(response, 'Objeto visible')
        self.assertNotContains(response, 'Objeto entregado')
        self.assertNotContains(response, 'Publicación pendiente')
        self.assertNotContains(response, 'Objeto sin publicación')

    def test_search_and_filters_support_subcategories_and_centers(self):
        matching = self.create_item(
            'Mesa auxiliar',
            description='Madera clara y patas metálicas.',
        )
        self.create_item('Silla deportiva', category=self.other_category)
        self.create_item('Mesa del sur', center=self.other_center)

        response = self.client.get(
            reverse('catalog:list'),
            {
                'q': 'madera',
                'category': self.parent_category.slug,
                'subcategory': self.category.slug,
                'center': self.center.slug,
            },
        )

        self.assertEqual(list(response.context['page_obj'].object_list), [matching])
        self.assertContains(response, 'Mesa auxiliar')
        self.assertNotContains(response, 'Mesa del sur')

    def test_subcategory_must_belong_to_selected_category(self):
        self.create_item('Objeto de hogar')

        response = self.client.get(
            reverse('catalog:list'),
            {
                'category': self.other_category.slug,
                'subcategory': self.category.slug,
            },
        )

        self.assertFalse(response.context['filter_form'].is_valid())
        self.assertEqual(response.context['paginator'].count, 0)
        self.assertContains(response, 'No hay resultados para esta búsqueda')

    def test_invalid_filter_values_are_rejected(self):
        self.create_item('Objeto que no debe filtrarse con ids falsos')

        response = self.client.get(
            reverse('catalog:list'),
            {'category': 'categoria-inexistente', 'center': 'centro-inexistente'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['filter_form'].is_valid())
        self.assertEqual(response.context['paginator'].count, 0)
        self.assertContains(response, 'No hay resultados para esta búsqueda')

    @override_settings(CATALOG_PAGE_SIZE=2)
    def test_pagination_preserves_search_and_filters(self):
        for index in range(3):
            self.create_item(f'Mesa paginada {index}')

        response = self.client.get(
            reverse('catalog:list'),
            {
                'q': 'Mesa',
                'category': self.parent_category.slug,
                'subcategory': self.category.slug,
                'center': self.center.slug,
            },
        )

        self.assertEqual(len(response.context['page_obj'].object_list), 2)
        self.assertTrue(response.context['page_obj'].has_next())
        self.assertContains(
            response,
            'q=Mesa&amp;category=hogar&amp;subcategory=muebles&amp;center=punto-limpio-norte&amp;page=2',
        )

        second_page = self.client.get(
            reverse('catalog:list'),
            {
                'q': 'Mesa',
                'category': self.parent_category.slug,
                'subcategory': self.category.slug,
                'center': self.center.slug,
                'page': 2,
            },
        )
        self.assertEqual(len(second_page.context['page_obj'].object_list), 1)

    def test_available_object_detail_shows_public_fields_and_photos(self):
        item = self.create_item('Mesa con fotografía', with_photo=True)
        photo = item.publication.photos.get()

        response = self.client.get(item.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, item.title)
        self.assertContains(response, item.description)
        self.assertContains(response, self.category.name)
        self.assertContains(response, self.center.name)
        self.assertContains(response, item.get_condition_display())
        self.assertContains(response, reverse('publication_photo', kwargs={'photo_id': photo.pk}))
        self.assertNotContains(response, self.owner.email)
        self.assertNotContains(response, item.internal_location)

    def test_unavailable_or_unpublished_object_detail_returns_404(self):
        delivered = self.create_item('Objeto ya entregado', item_status=ReusableObject.Status.DELIVERED)
        pending = self.create_item(
            'Objeto todavía pendiente',
            publication_status=Publication.Status.PENDING_REVIEW,
        )

        for item in (delivered, pending):
            with self.subTest(reference=item.reference):
                response = self.client.get(item.get_absolute_url())
                self.assertEqual(response.status_code, 404)

    def test_physical_validation_and_complete_public_data_are_required(self):
        pending_validation = self.create_item(
            'Pendiente de validación física',
            item_status=ReusableObject.Status.PENDING_VALIDATION,
        )
        incomplete = self.create_item('Objeto incompleto')
        InventoryItem.objects.filter(pk=incomplete.pk).update(description='   ')

        response = self.client.get(reverse('catalog:list'))

        self.assertNotContains(response, pending_validation.title)
        self.assertNotContains(response, incomplete.title)
        self.assertEqual(self.client.get(pending_validation.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(incomplete.get_absolute_url()).status_code, 404)

    def test_inactive_category_or_center_removes_object_from_catalog(self):
        inactive_category = Category.objects.create(
            name='Categoría desactivada',
            slug='categoria-desactivada',
        )
        category_item = self.create_item('Categoría no pública', category=inactive_category)
        inactive_category.is_active = False
        inactive_category.save()

        closed_center = RecyclingCenter.objects.create(
            name='Punto Limpio cerrado',
            slug='punto-limpio-cerrado',
        )
        center_item = self.create_item('Centro no operativo', center=closed_center)
        closed_center.operational_status = RecyclingCenter.OperationalStatus.CLOSED
        closed_center.save()

        response = self.client.get(reverse('catalog:list'))

        self.assertNotContains(response, category_item.title)
        self.assertNotContains(response, center_item.title)
        self.assertEqual(self.client.get(category_item.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(center_item.get_absolute_url()).status_code, 404)

    def test_withdrawal_immediately_hides_listing_detail_and_photo(self):
        item = self.create_item('Objeto retirado', with_photo=True)
        photo = item.publication.photos.get()
        Publication.objects.filter(pk=item.publication_id).update(
            status=Publication.Status.WITHDRAWN,
            withdrawn_at=timezone.now(),
        )

        response = self.client.get(reverse('catalog:list'))

        self.assertNotContains(response, item.title)
        self.assertEqual(self.client.get(item.get_absolute_url()).status_code, 404)
        self.assertEqual(
            self.client.get(reverse('publication_photo', args=[photo.pk])).status_code,
            404,
        )

    def test_empty_catalog_distinguishes_initial_and_filtered_states(self):
        initial = self.client.get(reverse('catalog:list'))
        filtered = self.client.get(reverse('catalog:list'), {'q': 'sin resultados'})

        self.assertContains(initial, 'Aún no hay objetos disponibles')
        self.assertContains(filtered, 'No hay resultados para esta búsqueda')
