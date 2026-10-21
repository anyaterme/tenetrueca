"""Synchronous citizen analysis and atomic, idempotent submission confirmation."""
import hashlib
import json
import time
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from assistant.errors import safe_error_details
from assistant.images import image_options
from assistant.models import PreparationPhoto, PublicationEvaluation, PublicationPreparation
from assistant.rules import PROMPT_VERSION, RULES_VERSION
from assistant.services import error_code, evaluate_once, schedule_evaluation
from assistant.snapshots import publication_snapshot, redact_personal_data, selected_classification
from audit.models import AuditEvent
from catalog.models import Category, public_category_ids
from publications.images import normalize_publication_photo
from publications.models import Publication, PublicationPhoto
from publications.services import user_can_submit_today


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def timeout_seconds():
    value = int(settings.AI_PREANALYSIS_TIMEOUT_SECONDS)
    if value < 1:
        raise ValidationError('El tiempo máximo del preanálisis debe ser positivo.')
    return value


def revision(publication):
    return {'fingerprint': publication_snapshot(publication)[0], 'status': publication.status,
            'updated_at': publication.updated_at.isoformat()}


def preparation_snapshot(preparation, data=None):
    data = data or preparation.working_data
    categories = list(Category.objects.filter(pk__in=public_category_ids()).order_by('pk').values(
        'id', 'name', 'parent_id',
    ))
    photos = list(preparation.photos.all())
    raw = {
        'title': data['title'], 'description': data['description'],
        'category_id': data['category_id'],
        'classification': selected_classification(SimpleNamespace(category_id=data['category_id']), categories),
        'categories': categories, 'rules_version': RULES_VERSION, 'prompt_version': PROMPT_VERSION,
        'photos': [photo.content_hash for photo in photos],
        'configuration': {
            'model': settings.OPENAI_MODEL or 'not-configured',
            'backend': settings.OPENAI_EVALUATION_BACKEND,
            'reasoning_effort': settings.OPENAI_REASONING_EFFORT,
            'images': {key: getattr(settings, key) for key in (
                'AI_IMAGE_MAX_DIMENSION', 'AI_IMAGE_JPEG_QUALITY', 'AI_IMAGE_FORMAT',
                'AI_IMAGE_DETAIL', 'AI_IMAGE_REMOVE_EXIF', 'AI_IMAGE_MAX_COUNT',
            )},
            'output_limit': settings.OPENAI_MAX_OUTPUT_TOKENS,
        },
    }
    fingerprint = digest(raw)
    raw['title'] = redact_personal_data(raw['title'])
    raw['description'] = redact_personal_data(raw['description'])
    return fingerprint, raw


def _save_photo(preparation, payload, *, image_format, width, height, sort_order, is_primary):
    # Older photo rows may predate stored image metadata. Normalize only our private
    # staging copy; existing draft files are never rewritten during preanalysis.
    if not image_format or not width or not height:
        normalized = normalize_publication_photo(ContentFile(payload))
        payload = normalized.content.read()
        image_format, width, height = normalized.image_format, normalized.width, normalized.height
    photo = PreparationPhoto(
        preparation=preparation,
        content_hash=hashlib.sha256(payload).hexdigest(), image_format=image_format,
        width=width, height=height, file_size=len(payload), sort_order=sort_order,
        is_primary=is_primary,
    )
    photo.image.save(f'photo.{image_format.lower()}', ContentFile(payload), save=False)
    try:
        photo.save()
    except Exception:
        photo.image.storage.delete(photo.image.name)
        raise
    return photo


def _delete_photos(photos):
    for photo in photos:
        storage, name = photo.image.storage, photo.image.name
        photo.delete()
        # Confirmed publications can own the same immutable private file.
        if not PublicationPhoto.objects.filter(image=name).exists():
            transaction.on_commit(lambda storage=storage, name=name: storage.delete(name))


def start_preparation(user, form, request_key, *, target=None):
    cleaned = form.cleaned_data
    data = {key: cleaned[key] for key in ('title', 'description')}
    data['category_id'] = cleaned['category'].pk
    uploaded = []
    for upload in cleaned.get('photos') or []:
        upload.seek(0)
        uploaded.append((upload.read(), upload._normalized_publication_photo))
    existing = list(target.photos.all()) if target else []
    original_revision = revision(target) if target else {}
    removed = {photo.pk for photo in cleaned.get('remove_photos') or []}
    existing = [photo for photo in existing if photo.pk not in removed]
    primary = cleaned.get('primary_photo') or ''
    request_digest = digest({
        **data, 'target': target.pk if target else None, 'primary': primary,
        'existing': [(photo.pk, photo.image.name, photo.updated_at.isoformat()) for photo in existing],
        'uploads': [hashlib.sha256(payload).hexdigest() for payload, _ in uploaded],
    })
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        previous = PublicationPreparation.objects.filter(owner=user, request_key=request_key).first()
        if previous:
            if previous.input_digest != request_digest:
                raise ValidationError('Esta solicitud ya se utilizó con otros datos. Recarga el formulario.')
            return previous
        if target:
            target = Publication.objects.select_for_update().get(pk=target.pk, submitter=user)
            if revision(target) != original_revision:
                raise ValidationError('La publicación cambió durante la preparación. Vuelve a intentarlo.')
            if target.status not in ('draft', 'changes_requested'):
                raise ValidationError('La publicación ya no se puede editar.')
        allowed, limit = user_can_submit_today(user)
        if not allowed:
            raise ValidationError(f'Has alcanzado el límite de {limit} publicaciones enviadas hoy.')
        ttl = int(settings.AI_PREPARATION_TTL_HOURS)
        if ttl < 1:
            raise ValidationError('La caducidad de la preparación debe ser positiva.')
        preparation = PublicationPreparation.objects.create(
            owner=user, request_key=request_key, input_digest=request_digest,
            target_publication=target, base_revision=revision(target) if target else {},
            original_data=data, working_data=data,
            expires_at=timezone.now() + timedelta(hours=ttl),
        )
        saved = []
        try:
            for index, photo in enumerate(existing):
                with photo.image.open('rb') as source:
                    payload = source.read(settings.PUBLICATION_PHOTO_MAX_BYTES + 1)
                if len(payload) > settings.PUBLICATION_PHOTO_MAX_BYTES:
                    raise ValidationError('La fotografía supera el tamaño máximo.')
                saved.append(_save_photo(
                    preparation, payload, image_format=photo.image_format,
                    width=photo.width, height=photo.height, sort_order=index,
                    is_primary=primary == f'existing:{photo.pk}',
                ))
            for index, (payload, normalized) in enumerate(uploaded):
                saved.append(_save_photo(
                    preparation, payload, image_format=normalized.image_format,
                    width=normalized.width, height=normalized.height,
                    sort_order=len(existing) + index, is_primary=primary == f'new:{index}',
                ))
            if saved and not any(photo.is_primary for photo in saved):
                first = next((item for item, original in zip(saved, existing) if original.is_primary), saved[0])
                first.is_primary = True
                first.save(update_fields=['is_primary'])
        except Exception:
            for photo in saved:
                photo.image.storage.delete(photo.image.name)
            raise
    return preparation


def run_preanalysis(preparation_id, *, retry=False, provider=None):
    started = time.monotonic()
    limit = timeout_seconds()
    with transaction.atomic():
        preparation = PublicationPreparation.objects.select_for_update().get(pk=preparation_id)
        ensure_open(preparation)
        latest = preparation.evaluations.first()
        fingerprint, snapshot = preparation_snapshot(preparation)
        if latest and (not retry or latest.status in ('pending', 'processing') or (
                latest.status == 'completed' and latest.content_fingerprint == fingerprint)):
            return latest
        evaluation = PublicationEvaluation.objects.create(
            preparation=preparation, requested_by=preparation.owner,
            origin='preanalysis', status='processing', started_at=timezone.now(), attempts=1,
            fingerprint=fingerprint, content_fingerprint=fingerprint, snapshot=snapshot,
            model=settings.OPENAI_MODEL or 'not-configured', rules_version=RULES_VERSION,
            prompt_version=PROMPT_VERSION,
        )
    try:
        photos = list(preparation.photos.all()[:image_options().max_count])
    except Exception as error:
        result, code, details = None, error_code(error), safe_error_details(error)
    else:
        remaining = limit - (time.monotonic() - started)
        if remaining <= 0:
            result, code, details = None, 'timeout_unknown_usage', {}
        else:
            result, code, details = evaluate_once(evaluation, photos, provider=provider,
                                                  timeout_seconds=remaining)
    # Also reject late results from simulated/custom providers which ignore the deadline.
    if time.monotonic() - started >= limit:
        result, code, details = None, 'timeout_unknown_usage', {}
    with transaction.atomic():
        preparation = PublicationPreparation.objects.select_for_update().get(pk=preparation_id)
        evaluation = PublicationEvaluation.objects.select_for_update().get(pk=evaluation.pk)
        if (preparation.status != 'open' or preparation.expires_at <= timezone.now()
                or evaluation.status != 'processing'
                or preparation_snapshot(preparation)[0] != fingerprint):
            evaluation.status = 'obsolete'
        else:
            evaluation.status = 'completed' if result is not None else 'error'
        evaluation.result = result or {}
        evaluation.error_code, evaluation.error_details = code, details
        evaluation.finished_at = timezone.now()
        evaluation.save(update_fields=['status', 'result', 'error_code', 'error_details', 'finished_at'])
    return evaluation


def ensure_open(preparation):
    if preparation.status != 'open' or preparation.expires_at <= timezone.now():
        raise ValidationError('La preparación ya no está disponible. Inicia un nuevo envío.')


def expire_interrupted_preanalysis(preparation):
    threshold = timezone.now() - timedelta(seconds=timeout_seconds() + 30)
    preparation.evaluations.filter(status='processing', started_at__lt=threshold).update(
        status='error', error_code='worker_interrupted_unknown_usage', finished_at=timezone.now(),
    )


@transaction.atomic
def update_preparation(preparation_id, form):
    preparation = PublicationPreparation.objects.select_for_update().get(pk=preparation_id)
    ensure_open(preparation)
    data = form.cleaned_data
    # A versioned form prevents stale tabs from overwriting a newer preparation.
    if data['version'] != digest(preparation.working_data):
        raise ValidationError('La propuesta cambió en otra pestaña. Recarga antes de continuar.')
    preparation.working_data = form.working_data()
    preparation.selected_suggestions = data['accepted_suggestions']
    preparation.save(update_fields=['working_data', 'selected_suggestions'])
    _delete_photos(list(preparation.photos.filter(pk__in=[p.pk for p in data['remove_photos']])))
    next_order = max((photo.sort_order for photo in preparation.photos.all()), default=-1) + 1
    added_photos = []
    for index, upload in enumerate(data['photos']):
        upload.seek(0)
        normalized = upload._normalized_publication_photo
        photo = _save_photo(preparation, upload.read(), image_format=normalized.image_format,
                            width=normalized.width, height=normalized.height,
                            sort_order=next_order + index, is_primary=False)
        preparation.working_data['added_photos'].append(photo.pk)
        added_photos.append(photo)
    photos = list(preparation.photos.all())
    selected = data.get('primary_photo')
    primary = next((photo for photo in photos if str(photo.pk) == selected), None)
    if selected and selected.startswith('new:'):
        primary = next((photo for i, photo in enumerate(added_photos) if selected == f'new:{i}'), None)
    primary = primary or next((photo for photo in photos if photo.is_primary), None) or photos[0]
    preparation.photos.update(is_primary=False)
    preparation.photos.filter(pk=primary.pk).update(is_primary=True)
    preparation.working_data['revision'] = str(uuid4())
    preparation.save(update_fields=['working_data'])
    return preparation


@transaction.atomic
def confirm_preparation(preparation_id, user, final_data, *, expected_version):
    get_user_model().objects.select_for_update().get(pk=user.pk)
    preparation = PublicationPreparation.objects.select_for_update().get(pk=preparation_id, owner=user)
    if preparation.status == 'confirmed':
        return preparation.submitted_publication
    ensure_open(preparation)
    if digest(preparation.working_data) != expected_version:
        raise ValidationError('La propuesta cambió antes de confirmar. Revisa el contenido y vuelve a enviarlo.')
    evaluation = preparation.evaluations.first()
    if not evaluation or evaluation.status not in ('completed', 'error'):
        raise ValidationError('Espera a que termine el preanálisis antes de enviar a revisión.')
    if evaluation.status == 'error' and preparation.selected_suggestions:
        raise ValidationError('No se pueden aplicar sugerencias de un análisis fallido.')
    allowed, limit = user_can_submit_today(user)
    if not allowed:
        raise ValidationError(f'Has alcanzado el límite de {limit} publicaciones enviadas hoy.')
    publication = None
    before = None
    if preparation.base_revision:
        publication = Publication.objects.select_for_update().filter(
            pk=preparation.target_publication_id, submitter=user,
        ).first()
        if publication is None or revision(publication) != preparation.base_revision:
            raise ValidationError('La publicación original cambió. Inicia de nuevo el envío.')
        if publication.status not in ('draft', 'changes_requested'):
            raise ValidationError('La publicación ya no se puede editar.')
        before = {'title': publication.title, 'description': publication.description,
                  'category_id': publication.category_id, 'status': publication.status}
    category = Category.objects.filter(pk=final_data['category_id']).first()
    if (not category or not category.is_available_for_publication()
            or category.children.filter(is_active=True).exists()):
        raise ValidationError('Selecciona una categoría y subcategoría disponibles.')
    publication = publication or Publication(submitter=user)
    publication.title, publication.description = final_data['title'], final_data['description']
    publication.category = category
    publication.status = Publication.Status.PENDING_REVIEW
    publication.submitted_at = publication.status_changed_at = timezone.now()
    publication.reviewed_at = None
    publication.save()
    old_photos = list(publication.photos.all())
    publication.photos.all().delete()
    for photo in preparation.photos.all():
        PublicationPhoto.objects.create(
            publication=publication, image=photo.image.name, image_format=photo.image_format,
            width=photo.width, height=photo.height, file_size=photo.file_size,
            sort_order=photo.sort_order, is_primary=photo.is_primary,
            alt_text=f'Fotografía de {publication.title}',
        )
    for photo in old_photos:
        storage, name = photo.image.storage, photo.image.name
        transaction.on_commit(lambda storage=storage, name=name: storage.delete(name))
    fingerprint, _ = preparation_snapshot(preparation, final_data)
    reusable = (evaluation.status == 'completed' and evaluation.content_fingerprint == fingerprint
                and evaluation.model == (settings.OPENAI_MODEL or 'not-configured')
                and evaluation.rules_version == RULES_VERSION and evaluation.prompt_version == PROMPT_VERSION
                and evaluation.is_simulated == (settings.OPENAI_EVALUATION_BACKEND == 'mock'))
    # Preserve the analyzed snapshot. Only bind its identity to the final publication
    # when its raw content and analysis configuration are exactly the same.
    if evaluation.status == 'completed':
        evaluation.publication = publication
        if reusable:
            evaluation.fingerprint = publication_snapshot(publication)[0]
        evaluation.save(update_fields=['publication', 'fingerprint'])
    preparation.status = 'confirmed'
    preparation.confirmed_data = final_data
    preparation.submitted_publication = publication
    preparation.save(update_fields=['status', 'confirmed_data', 'submitted_publication'])
    AuditEvent.objects.create(
        actor=user, action='publication.submitted', entity='Publication', entity_id=str(publication.pk),
        source='web', before=before, after={**final_data, 'status': publication.status},
        metadata={'preparation_id': str(preparation.pk), 'preanalysis_id': evaluation.pk,
                  'accepted_suggestions': preparation.selected_suggestions, 'preanalysis_reused': reusable,
                  'discarded_suggestions': preparation.working_data.get('discarded_suggestions', []),
                  'preanalysis_status': evaluation.status, 'preanalysis_error_code': evaluation.error_code,
                  'original_data': preparation.original_data},
    )
    if not reusable:
        schedule_evaluation(publication.pk)
    return publication


@transaction.atomic
def discard_preparation(preparation_id, *, expired=False):
    preparation = PublicationPreparation.objects.select_for_update().get(pk=preparation_id)
    if preparation.status == 'confirmed' and not expired:
        return preparation
    if preparation.status == 'open':
        preparation.status = 'expired' if expired else 'cancelled'
        preparation.evaluations.filter(status__in=['pending', 'processing']).update(status='obsolete')
    _delete_photos(list(preparation.photos.all()))
    preparation.original_data = preparation.working_data = {}
    preparation.save(update_fields=['status', 'original_data', 'working_data'])
    return preparation
