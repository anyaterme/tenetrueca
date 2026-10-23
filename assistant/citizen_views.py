from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from assistant.forms import PreparationReviewForm
from assistant.models import PreparationPhoto, PublicationPreparation
from assistant.schema import CHECK_LABELS, OVERALL_LABELS
from assistant.preparations import (
    confirm_preparation, digest, discard_preparation, expire_interrupted_preanalysis,
    run_preanalysis, timeout_seconds, update_preparation,
    preparation_snapshot,
)
from catalog.models import Category
from publications.images import ALLOWED_IMAGE_FORMATS


@login_required
def preparation_review(request, token):
    preparation = get_object_or_404(PublicationPreparation, pk=token, owner=request.user)
    if preparation.status == 'confirmed' and preparation.submitted_publication_id:
        return redirect('publications:detail', pk=preparation.submitted_publication_id)
    if preparation.status != 'open' or preparation.expires_at <= timezone.now():
        if preparation.status == 'open':
            discard_preparation(preparation.pk, expired=True)
        messages.info(request, 'La preparación ya no está disponible. Inicia un nuevo envío.')
        return redirect('publications:list')
    action = request.POST.get('action') if request.method == 'POST' else None
    if action == 'cancel':
        discarded = discard_preparation(preparation.pk)
        if discarded.status == 'confirmed' and discarded.submitted_publication_id:
            return redirect('publications:detail', pk=discarded.submitted_publication_id)
        messages.info(request, 'Envío cancelado. No se ha enviado el objeto a revisión.')
        return redirect('publications:detail', pk=preparation.target_publication_id) if preparation.target_publication_id else redirect('publications:list')
    expire_interrupted_preanalysis(preparation)
    evaluation = preparation.evaluations.first()
    photo_first = preparation.working_data.get('flow') == 'photo_first'
    form = PreparationReviewForm(
        request.POST if request.method == 'POST' else None, request.FILES or None,
        preparation=preparation, evaluation=evaluation, action=action,
    )
    preview = None
    saved = False
    if request.method == 'POST':
        allowed_actions = ('analyze_photos', 'confirm_selected') if photo_first else (
            'preview', 'retry', 'accept_applicable', 'confirm_all', 'confirm_selected', 'confirm_none')
        if action not in allowed_actions:
            form.add_error(None, 'Selecciona una acción válida.')
        elif action.startswith('confirm') and (not evaluation or evaluation.status not in ('completed', 'error')):
            form.add_error(None, 'Espera a que termine el preanálisis antes de enviar a revisión.')
        elif form.is_valid():
            try:
                preparation = update_preparation(preparation.pk, form)
                saved = True
                photos_changed = photo_first and (not evaluation or
                    evaluation.content_fingerprint != preparation_snapshot(preparation)[0])
                if action in ('retry', 'analyze_photos') or photos_changed:
                    run_preanalysis(preparation.pk, retry=True)
                    url = reverse('publications:preparation', kwargs={'token': preparation.pk})
                    return JsonResponse({'url': url}) if request.headers.get('X-Requested-With') == 'XMLHttpRequest' else redirect(url)
                if action.startswith('confirm'):
                    publication = confirm_preparation(preparation.pk, request.user, form.final_data,
                                                      expected_version=digest(preparation.working_data))
                    messages.success(request, 'Tu objeto se ha enviado a revisión.')
                    return redirect('publications:detail', pk=publication.pk)
                preview = form.final_data if action == 'preview' else None
                form = PreparationReviewForm(preparation=preparation, evaluation=evaluation)
            except ValidationError as error:
                preparation.refresh_from_db()
                if preparation.status == 'confirmed' and preparation.submitted_publication_id:
                    return redirect('publications:detail', pk=preparation.submitted_publication_id)
                if saved:
                    messages.error(request, ' '.join(error.messages))
                    return redirect('publications:preparation', token=preparation.pk)
                form.add_error(None, error)
    if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
    rows = form.suggestion_rows()
    has_summary = bool(evaluation and evaluation.status == 'completed' and (photo_first or any(
        evaluation.result.get(key) for key in ('issues', 'unverifiable', 'recommendations'))))
    return render(request, 'publications/preparation_review.html', {
        'preparation': preparation, 'evaluation': evaluation, 'form': form,
        'suggestion_rows': rows, 'photo_rows': form.photo_rows(), 'preview': preview,
        'has_summary': has_summary, 'photo_step': 3 if rows else 2, 'summary_step': 4 if rows else 3,
        'has_applicable_suggestions': any(row['applicable'] for row in rows),
        'photo_max_count': settings.PUBLICATION_PHOTO_MAX_COUNT,
        'photo_max_bytes': settings.PUBLICATION_PHOTO_MAX_BYTES,
        'photo_max_mb': settings.PUBLICATION_PHOTO_MAX_BYTES // (1024 * 1024),
        'preview_category': Category.objects.filter(pk=preview['category_id']).first() if preview else None,
        'preanalysis_timeout': timeout_seconds(),
        'photo_first': photo_first,
        'photo_overall': OVERALL_LABELS.get(evaluation.result.get('overall'), '') if evaluation else '',
        'photo_checks': [{'label': label, **evaluation.result['checks'][key]} for key, label in CHECK_LABELS.items()]
                        if photo_first and evaluation and evaluation.status == 'completed' else [],
    })


@login_required
def preparation_photo(request, token, photo_id):
    photo = get_object_or_404(PreparationPhoto, pk=photo_id, preparation_id=token,
                              preparation__owner=request.user, preparation__status='open',
                              preparation__expires_at__gt=timezone.now())
    try:
        source = photo.image.storage.open(photo.image.name, 'rb')
    except (OSError, FileNotFoundError):
        raise Http404 from None
    response = FileResponse(source, content_type=ALLOWED_IMAGE_FORMATS[photo.image_format][1])
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
