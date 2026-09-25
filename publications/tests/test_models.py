from datetime import timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from catalog.models import Category, ReusableObject
from locations.models import RecyclingCenter
from publications.forms import PublicationForm
from publications.models import Publication, PublicationPhoto


def image_upload(image_format='JPEG', size=(32, 24), name='original-name.bin', exif=None):
    image = Image.new('RGB', size, '#1673b8')
    payload = BytesIO()
    save_options = {'exif': exif} if exif is not None else {}
    image.save(payload, format=image_format, **save_options)
    return SimpleUploadedFile(name, payload.getvalue(), content_type='application/octet-stream')


def animated_png_upload():
    first = Image.new('RGBA', (24, 24), '#1673b8')
    second = Image.new('RGBA', (24, 24), '#59b300')
    payload = BytesIO()
    first.save(
        payload,
        format='PNG',
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
    )
    return SimpleUploadedFile('animated.png', payload.getvalue(), content_type='image/png')


class PublicationModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email='publisher@example.com',
            first_name='Publisher',
            password='test-password',
        )
        cls.category = Category.objects.create(name='Hogar', slug='hogar')
        cls.publication = Publication.objects.create(
            submitter=cls.user,
            title='Mesa auxiliar',
            description='Mesa en buen estado.',
            category=cls.category,
        )

    def test_related_history_protects_user_and_category(self):
        with self.assertRaises(ProtectedError):
            self.user.delete()
        with self.assertRaises(ProtectedError):
            self.category.delete()

    def test_only_one_primary_photo_is_allowed(self):
        with TemporaryDirectory() as private_media:
            with override_settings(PRIVATE_MEDIA_ROOT=private_media):
                PublicationPhoto.objects.create(
                    publication=self.publication,
                    image=image_upload(name='mesa-1.jpg'),
                    is_primary=True,
                )

                with self.assertRaises((IntegrityError, ValidationError)), transaction.atomic():
                    PublicationPhoto.objects.create(
                        publication=self.publication,
                        image=image_upload(name='mesa-2.jpg'),
                        is_primary=True,
                    )

    def test_inactive_category_cannot_be_used_for_new_publication(self):
        inactive = Category.objects.create(name='Inactiva', slug='inactiva', is_active=False)

        with self.assertRaises(ValidationError):
            Publication.objects.create(
                submitter=self.user,
                title='Objeto sin categoría válida',
                description='No debe guardarse.',
                category=inactive,
            )

    def test_child_of_inactive_category_cannot_be_used(self):
        parent = Category.objects.create(name='Padre', slug='padre', is_active=False)
        child = Category.objects.create(name='Hija', slug='hija', parent=parent)

        with self.assertRaises(ValidationError):
            Publication.objects.create(
                submitter=self.user,
                title='Objeto en subcategoría inactiva',
                description='No debe guardarse.',
                category=child,
            )

    def test_publication_form_only_lists_active_categories(self):
        inactive = Category.objects.create(name='Inactiva', slug='inactiva', is_active=False)
        form = PublicationForm()

        self.assertIn(self.category, form.fields['category'].queryset)
        self.assertNotIn(inactive, form.fields['category'].queryset)


class PublicationPhotoSecurityTests(TestCase):
    def setUp(self):
        self.private_media = TemporaryDirectory()
        self.settings_override = override_settings(PRIVATE_MEDIA_ROOT=self.private_media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.private_media.cleanup)

        User = get_user_model()
        self.user = User.objects.create_user(
            email='photo-owner@example.com',
            first_name='Photo owner',
            password='test-password',
        )
        self.validator = User.objects.create_user(
            email='photo-validator@example.com',
            first_name='Validator',
            password='test-password',
        )
        self.category = Category.objects.create(name='Fotografía', slug='fotografia')
        self.publication = Publication.objects.create(
            submitter=self.user,
            title='Objeto fotografiado',
            description='Descripción.',
            category=self.category,
        )

    def create_photo(self, upload=None, **kwargs):
        return PublicationPhoto.objects.create(
            publication=self.publication,
            image=upload or image_upload(),
            **kwargs,
        )

    def test_jpeg_png_and_webp_are_decoded_and_normalized(self):
        for image_format in ('JPEG', 'PNG', 'WEBP'):
            with self.subTest(image_format=image_format):
                photo = self.create_photo(image_upload(image_format=image_format))
                self.assertEqual(photo.image_format, image_format)
                self.assertEqual((photo.width, photo.height), (32, 24))
                self.assertGreater(photo.file_size, 0)
                self.assertNotIn('original-name', photo.image.name)
                with photo.image.storage.open(photo.image.name, 'rb') as stored:
                    with Image.open(stored) as decoded:
                        decoded.load()
                        self.assertEqual(decoded.format, image_format)
                        self.assertFalse(decoded.getexif())

    def test_orientation_is_corrected_and_exif_is_removed(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = 'metadata privada'
        photo = self.create_photo(image_upload(size=(40, 20), exif=exif))

        self.assertEqual((photo.width, photo.height), (20, 40))
        with photo.image.storage.open(photo.image.name, 'rb') as stored:
            with Image.open(stored) as decoded:
                self.assertFalse(decoded.getexif())

    def test_svg_unsupported_and_animated_files_are_rejected(self):
        uploads = (
            SimpleUploadedFile('image.svg', b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', content_type='image/svg+xml'),
            image_upload(image_format='GIF', name='image.gif'),
            animated_png_upload(),
        )
        for upload in uploads:
            with self.subTest(filename=upload.name), self.assertRaises(ValidationError):
                self.create_photo(upload)

    def test_fake_and_corrupt_images_are_rejected(self):
        for payload in (b'not an image', b'\x89PNG\r\n\x1a\ntruncated'):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                self.create_photo(SimpleUploadedFile('fake.jpg', payload, content_type='image/jpeg'))

    def test_file_size_dimensions_and_photo_count_limits(self):
        with override_settings(PUBLICATION_PHOTO_MAX_BYTES=16):
            with self.assertRaises(ValidationError):
                self.create_photo(image_upload())

        with override_settings(PUBLICATION_PHOTO_MAX_WIDTH=10):
            with self.assertRaises(ValidationError):
                self.create_photo(image_upload(size=(20, 10)))

        with override_settings(
            PUBLICATION_PHOTO_MAX_WIDTH=100,
            PUBLICATION_PHOTO_MAX_HEIGHT=100,
            PUBLICATION_PHOTO_MAX_PIXELS=100,
        ):
            with self.assertRaises(ValidationError):
                self.create_photo(image_upload(size=(11, 10)))

        with override_settings(PUBLICATION_PHOTO_MAX_COUNT=1):
            self.create_photo(image_upload())
            with self.assertRaises(ValidationError):
                self.create_photo(image_upload())

    def test_pending_photo_is_private_until_publication_and_object_are_approved(self):
        photo = self.create_photo(image_upload())
        url = reverse('publication_photo', kwargs={'photo_id': photo.pk})

        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.user)
        owner_response = self.client.get(url)
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(owner_response['Cache-Control'], 'private, no-store')
        self.client.logout()

        self.publication.status = Publication.Status.APPROVED
        self.publication.approved_at = timezone.now()
        self.publication.save()
        self.assertEqual(self.client.get(url).status_code, 404)

        center = RecyclingCenter.objects.create(name='Centro', slug='centro')
        ReusableObject.objects.create(
            reference='PHOTO-OBJECT-1',
            owner=self.user,
            publication=self.publication,
            center=center,
            title=self.publication.title,
            description=self.publication.description,
            category=self.category.name,
            category_node=self.category,
            internal_location='A-01',
            condition=ReusableObject.Condition.GOOD,
            status=ReusableObject.Status.AVAILABLE,
            received_at=timezone.now() - timedelta(days=1),
            validated_at=timezone.now(),
            validated_by=self.validator,
        )

        public_response = self.client.get(url)
        self.assertEqual(public_response.status_code, 200)
        self.assertEqual(public_response['Content-Type'], 'image/jpeg')
        self.assertEqual(public_response['Cache-Control'], 'public, max-age=3600')

    def test_private_storage_does_not_publish_a_direct_media_url(self):
        photo = self.create_photo(image_upload())

        with self.assertRaises(ValueError):
            _url = photo.image.url
        self.assertNotEqual(Path(photo.image.path).parent, Path(settings.MEDIA_ROOT))
