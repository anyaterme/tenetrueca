import base64
import hashlib
import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image, PngImagePlugin

from assistant.fixtures import simulated_response
from assistant.images import ImagePreparationError, image_options, optimize_image
from assistant.models import PublicationEvaluation
from assistant.providers import OpenAIProvider, ProviderResponse, request_content
from assistant.services import enqueue_evaluation, process_evaluation
from assistant.snapshots import publication_snapshot
from catalog.models import Category
from core.roles import ROLE_STAFF_ADMIN
from publications.models import Publication, PublicationPhoto


def encoded_image(size=(1600, 800), *, mode='RGB', color='red', image_format='PNG', **kwargs):
    with BytesIO() as buffer, Image.new(mode, size, color) as image:
        image.save(buffer, image_format, **kwargs)
        return buffer.getvalue()


def memory_photo(payload):
    # Reopen a fresh read-only source as storage backends do, without writing any file.
    return SimpleNamespace(image=Mock(open=Mock(side_effect=lambda mode: BytesIO(payload))))


@override_settings(AI_IMAGE_MAX_DIMENSION=1024, AI_IMAGE_JPEG_QUALITY=80,
                   AI_IMAGE_FORMAT='JPEG', AI_IMAGE_DETAIL='high',
                   AI_IMAGE_REMOVE_EXIF=True, AI_IMAGE_MAX_COUNT=3)
class ImageOptimizationTests(SimpleTestCase):
    def test_landscape_portrait_aspect_ratio_and_small_images(self):
        for source_size, expected in [((1600, 800), (1024, 512)),
                                      ((800, 1600), (512, 1024)),
                                      ((1500, 1000), (1024, 683)),
                                      ((240, 120), (240, 120))]:
            with self.subTest(size=source_size):
                with optimize_image(memory_photo(encoded_image(source_size))) as output:
                    self.assertIsInstance(output, BytesIO)
                    self.assertEqual(output.tell(), 0)
                    with Image.open(output) as result:
                        self.assertEqual(result.size, expected)
                        self.assertEqual(result.format, 'JPEG')
                        self.assertEqual(result.mode, 'RGB')

    def test_exif_orientation_is_applied_before_resizing_and_metadata_removed(self):
        exif = Image.Exif()
        exif[274] = 6  # Rotate 90 degrees clockwise.
        exif[315] = 'private-author'
        exif[34853] = {1: 'N'}  # GPS IFD.
        with BytesIO() as buffer, Image.new('RGB', (1600, 800), 'red') as source:
            source.paste('blue', (800, 0, 1600, 800))
            source.save(buffer, 'JPEG', exif=exif,
                        icc_profile=b'private-icc', comment=b'private-comment')
            payload = buffer.getvalue()
        with optimize_image(memory_photo(payload)) as output:
            raw = output.getvalue()
            with Image.open(output) as result:
                self.assertEqual(result.size, (512, 1024))
                self.assertGreater(result.getpixel((256, 100))[0], 240)
                self.assertGreater(result.getpixel((256, 900))[2], 240)
                self.assertFalse(result.getexif())
                self.assertFalse({'exif', 'icc_profile', 'comment', 'gps'} & result.info.keys())
            self.assertNotIn(b'private-', raw)

    def test_png_text_and_transparency_are_removed_on_white_background(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('GPS', 'private-coordinates')
        for mode in ('RGBA', 'LA', 'P'):
            with self.subTest(mode=mode):
                color = (255, 0, 0, 0) if mode == 'RGBA' else (0, 0) if mode == 'LA' else 0
                extra = {'transparency': 0} if mode == 'P' else {}
                payload = encoded_image((20, 20), mode=mode, color=color, pnginfo=metadata, **extra)
                with optimize_image(memory_photo(payload)) as output:
                    self.assertNotIn(b'private-coordinates', output.getvalue())
                    with Image.open(output) as result:
                        self.assertEqual(result.getpixel((10, 10)), (255, 255, 255))

    def test_other_supported_color_modes_convert_to_rgb(self):
        for mode, image_format, color in [('L', 'PNG', 128), ('CMYK', 'JPEG', (0, 255, 255, 0))]:
            with self.subTest(mode=mode):
                with optimize_image(memory_photo(encoded_image((20, 30), mode=mode,
                                    color=color, image_format=image_format))) as output:
                    with Image.open(output) as result:
                        self.assertEqual(result.mode, 'RGB')
                        self.assertEqual(result.size, (20, 30))

    def test_transparent_webp_is_supported(self):
        payload = encoded_image((20, 30), mode='RGBA', color=(255, 0, 0, 0), image_format='WEBP')
        with optimize_image(memory_photo(payload)) as output, Image.open(output) as result:
            self.assertEqual(result.getpixel((10, 10)), (255, 255, 255))

    def test_quality_and_dimension_are_configurable_without_upscaling(self):
        payload = encoded_image()
        with self.settings(AI_IMAGE_MAX_DIMENSION=640, AI_IMAGE_JPEG_QUALITY=50):
            with optimize_image(memory_photo(payload)) as output:
                with Image.open(output) as result:
                    self.assertEqual(result.size, (640, 320))
                    low_quality_tables = result.quantization
        with optimize_image(memory_photo(payload)) as output:
            with Image.open(output) as result:
                self.assertNotEqual(result.quantization, low_quality_tables)
        self.assertEqual(image_options(max_side=256).max_dimension, 256)
        self.assertEqual(image_options(max_side=2048).max_dimension, 1024)

    def test_invalid_configuration_fails_before_opening_any_photo(self):
        for key, value in [('AI_IMAGE_MAX_DIMENSION', 'invalid'), ('AI_IMAGE_MAX_DIMENSION', 0),
                           ('AI_IMAGE_JPEG_QUALITY', 96), ('AI_IMAGE_JPEG_QUALITY', 0),
                           ('AI_IMAGE_MAX_COUNT', 0), ('AI_IMAGE_MAX_COUNT', 9),
                           ('AI_IMAGE_FORMAT', 'PNG'), ('AI_IMAGE_REMOVE_EXIF', False),
                           ('AI_IMAGE_DETAIL', 'unsupported')]:
            with self.subTest(setting=key, value=value), self.settings(**{key: value}):
                photo = memory_photo(encoded_image((20, 20)))
                with self.assertRaises(ImagePreparationError) as failure:
                    optimize_image(photo)
                self.assertEqual(failure.exception.details['reason'], 'configuration')
                photo.image.open.assert_not_called()

    def test_corrupt_empty_truncated_and_disallowed_files(self):
        inputs = [(b'', 'file_size'), (b'private corrupt content', 'unreadable'),
                  (encoded_image(image_format='JPEG')[:-200], 'unreadable'),
                  (encoded_image((20, 20), image_format='GIF'), 'format')]
        for payload, reason in inputs:
            with self.subTest(reason=reason), self.assertRaises(ImagePreparationError) as failure:
                optimize_image(memory_photo(payload))
            self.assertEqual(failure.exception.details, {'stage': 'images', 'reason': reason})

    def test_actual_bytes_and_pixel_limits_are_checked_before_decoding(self):
        with self.settings(PUBLICATION_PHOTO_MAX_BYTES=10):
            with self.assertRaises(ImagePreparationError) as failure:
                optimize_image(memory_photo(b'x' * 11))
            self.assertEqual(failure.exception.details['reason'], 'file_size')
        for limits in ({'PUBLICATION_PHOTO_MAX_PIXELS': 100},
                       {'PUBLICATION_PHOTO_MAX_WIDTH': 10}, {'PUBLICATION_PHOTO_MAX_HEIGHT': 10}):
            with self.subTest(limits=limits), self.settings(**limits):
                with self.assertRaises(ImagePreparationError) as failure:
                    optimize_image(memory_photo(encoded_image((20, 20))))
                self.assertEqual(failure.exception.details['reason'], 'dimensions')
        with patch.object(Image, 'MAX_IMAGE_PIXELS', 300):
            with self.assertRaises(ImagePreparationError) as failure:
                optimize_image(memory_photo(encoded_image((20, 20))))
            self.assertEqual(failure.exception.details['reason'], 'dimensions')

    def test_animation_is_rejected_instead_of_silently_dropping_frames(self):
        with BytesIO() as buffer, Image.new('RGB', (20, 20), 'red') as first:
            with Image.new('RGB', (20, 20), 'blue') as second:
                first.save(buffer, 'PNG', save_all=True, append_images=[second])
            with self.assertRaises(ImagePreparationError) as failure:
                optimize_image(memory_photo(buffer.getvalue()))
            self.assertEqual(failure.exception.details['reason'], 'animated')


@override_settings(OPENAI_EVALUATION_BACKEND='mock', OPENAI_MODEL='fixture-model',
                   AI_IMAGE_MAX_DIMENSION=1024, AI_IMAGE_JPEG_QUALITY=80,
                   AI_IMAGE_FORMAT='JPEG', AI_IMAGE_DETAIL='high',
                   AI_IMAGE_REMOVE_EXIF=True, AI_IMAGE_MAX_COUNT=3,
                   OPENAI_INPUT_USD_PER_MILLION=Decimal('1'),
                   OPENAI_OUTPUT_USD_PER_MILLION=Decimal('2'), OPENAI_REQUEST_RESERVE_USD=Decimal('.1'))
class ImageEvaluationIntegrationTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory(prefix='trueca-ai-images-')
        self.addCleanup(media.cleanup)
        self.storage_settings = override_settings(PRIVATE_MEDIA_ROOT=media.name)
        self.storage_settings.enable()
        self.addCleanup(self.storage_settings.disable)
        user = get_user_model().objects.create_user(username='image-reviewer', email='reviewer@example.test')
        user.groups.add(Group.objects.get_or_create(name=ROLE_STAFF_ADMIN)[0])
        self.client.force_login(user)
        category = Category.objects.create(name='Juguetes', slug='ai-image-toys')
        self.publication = Publication.objects.create(
            submitter=user, title='Robot', description='Figura con accesorios.', category=category,
            status='pending_review', submitted_at=timezone.now(),
        )
        self.photos = []
        for order in range(5):
            self.photos.append(PublicationPhoto.objects.create(
                publication=self.publication, sort_order=order, is_primary=(order == 4),
                image=SimpleUploadedFile(f'photo-{order}.png', encoded_image(), content_type='image/png'),
            ))

    def provider_response(self):
        return Mock(status='completed', output_text=json.dumps(simulated_response('suitable')),
                    id='mock-response', usage=Mock(input_tokens=50, output_tokens=100),
                    output=[], incomplete_details=None)

    def test_sdk_receives_only_three_optimized_images_primary_first_and_originals_intact(self):
        original_state = []
        for photo in self.photos:
            with photo.image.open('rb') as original:
                original_state.append((photo.image.name, hashlib.sha256(original.read()).hexdigest(),
                                       photo.updated_at, photo.file_size, photo.width, photo.height))
        evaluation = enqueue_evaluation(self.publication.pk)[0]
        with patch('assistant.providers.require_live_access'), patch('openai.OpenAI') as client_type:
            client = client_type.return_value.__enter__.return_value
            client.responses.create.return_value = self.provider_response()
            with patch('assistant.providers.optimize_image', wraps=optimize_image) as optimize:
                process_evaluation(evaluation.pk, provider=OpenAIProvider())
                process_evaluation(evaluation.pk, provider=OpenAIProvider())
                self.assertEqual([call.args[0].pk for call in optimize.call_args_list],
                                 [self.photos[4].pk, self.photos[0].pk, self.photos[1].pk])
            client.responses.create.assert_called_once()
            images = client.responses.create.call_args.kwargs['input'][0]['content'][1:]
            self.assertEqual(len(images), 3)
            for content in images:
                self.assertEqual(content['detail'], 'high')
                self.assertTrue(content['image_url'].startswith('data:image/jpeg;base64,'))
                with Image.open(BytesIO(base64.b64decode(content['image_url'].split(',', 1)[1]))) as image:
                    self.assertEqual(image.size, (1024, 512))
                    self.assertEqual(image.format, 'JPEG')
                    self.assertFalse(image.getexif())
        for photo, state in zip(self.photos, original_state):
            photo.refresh_from_db()
            with photo.image.open('rb') as original:
                self.assertEqual((photo.image.name, hashlib.sha256(original.read()).hexdigest(),
                                  photo.updated_at, photo.file_size, photo.width, photo.height), state)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, 'pending_review')
        self.assertFalse(self.publication.moderation_decisions.exists())

    def test_configurable_limit_and_detail_and_direct_provider_cannot_exceed_limit(self):
        evaluation = enqueue_evaluation(self.publication.pk)[0]
        with self.settings(AI_IMAGE_MAX_COUNT=1, AI_IMAGE_DETAIL='low'):
            with patch('assistant.providers.optimize_image', wraps=optimize_image) as optimize:
                content = request_content(evaluation, iter(self.photos))
                self.assertEqual(optimize.call_count, 1)
            self.assertEqual(len(content), 2)
            self.assertEqual(content[1]['detail'], 'low')

    def test_manual_check_keeps_one_small_image_low_detail_and_one_request(self):
        evaluation = enqueue_evaluation(self.publication.pk, manual=True)[0]
        with patch('assistant.providers.require_live_access'), patch('openai.OpenAI') as client_type:
            client = client_type.return_value.__enter__.return_value
            client.responses.create.return_value = self.provider_response()
            process_evaluation(evaluation.pk, provider=OpenAIProvider(), manual=True, max_side=512)
            client.responses.create.assert_called_once()
            content = client.responses.create.call_args.kwargs['input'][0]['content']
            self.assertEqual(len(content), 2)
            self.assertEqual(content[1]['detail'], 'low')
            with Image.open(BytesIO(base64.b64decode(content[1]['image_url'].split(',', 1)[1]))) as image:
                self.assertEqual(image.size, (512, 256))

    def test_mock_flow_never_reads_or_optimizes_photos(self):
        evaluation = enqueue_evaluation(self.publication.pk)[0]
        with patch('assistant.providers.optimize_image') as optimize, patch('openai.OpenAI') as client:
            process_evaluation(evaluation.pk)
            optimize.assert_not_called()
            client.assert_not_called()
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'completed')

    def test_preparation_failure_is_sanitized_no_sdk_call_and_manual_review_available(self):
        # Corrupt the selected photo only in isolated test storage.
        with self.photos[4].image.storage.open(self.photos[4].image.name, 'wb') as source:
            source.write(b'private-corrupt-image')
        evaluation = enqueue_evaluation(self.publication.pk)[0]
        with patch('assistant.providers.require_live_access'), patch('openai.OpenAI') as client:
            with self.assertLogs('assistant.services', level='WARNING') as logs:
                process_evaluation(evaluation.pk, provider=OpenAIProvider())
            client.assert_not_called()
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, 'error')
        self.assertEqual(evaluation.error_code, 'image_preparation_failed')
        self.assertEqual(evaluation.error_details, {'stage': 'images', 'reason': 'unreadable'})
        self.assertNotIn('private-corrupt', str(logs.output) + json.dumps(evaluation.error_details))
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, 'pending_review')
        response = self.client.get(reverse('moderation:detail', args=[self.publication.pk]))
        self.assertContains(response, 'La revisión manual sigue disponible.')
        self.assertContains(response, 'Registrar decisión')
        self.assertFalse(self.publication.moderation_decisions.exists())

    def test_deduplication_refresh_and_explicit_repeat_do_not_add_unrequested_calls(self):
        evaluation = enqueue_evaluation(self.publication.pk)[0]
        provider = Mock(is_simulated=True)
        provider.evaluate.return_value = ProviderResponse(simulated_response('suitable'))
        process_evaluation(evaluation.pk, provider=provider)
        fingerprint = evaluation.fingerprint
        for _ in range(2):
            same, created = enqueue_evaluation(self.publication.pk)
            self.assertFalse(created)
            self.assertEqual(same.pk, evaluation.pk)
            self.client.get(reverse('moderation:queue'))
            self.client.get(reverse('moderation:detail', args=[self.publication.pk]))
        self.assertEqual(PublicationEvaluation.objects.count(), 1)
        provider.evaluate.assert_called_once()
        self.assertEqual(publication_snapshot(self.publication)[0], fingerprint)
        PublicationEvaluation.objects.filter(pk=evaluation.pk).update(
            created_at=timezone.now() - timedelta(minutes=2))
        repeat, created = enqueue_evaluation(self.publication.pk, force=True)
        self.assertTrue(created)
        self.assertNotEqual(repeat.pk, evaluation.pk)
        process_evaluation(repeat.pk, provider=provider)
        self.assertEqual(provider.evaluate.call_count, 2)

    def test_changing_primary_photo_invalidates_cached_image_selection(self):
        before, snapshot = publication_snapshot(self.publication)
        self.assertEqual(snapshot['photos'][0]['id'], self.photos[4].pk)
        PublicationPhoto.objects.filter(publication=self.publication).update(is_primary=False)
        PublicationPhoto.objects.filter(pk=self.photos[2].pk).update(is_primary=True)
        after, snapshot = publication_snapshot(self.publication)
        self.assertNotEqual(before, after)
        self.assertEqual(snapshot['photos'][0]['id'], self.photos[2].pk)
