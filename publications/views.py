from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max, Prefetch
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from audit.models import AuditEvent
from catalog.models import Category
from core.permissions import scope_publications, user_can_moderate, user_can_receive
from moderation.models import ModerationDecision
from publications.forms import (
    CitizenPublicationForm,
    PublicationStatusFilterForm,
    PublicationWithdrawalForm,
)
from publications.images import ALLOWED_IMAGE_FORMATS
from publications.models import Publication, PublicationPhoto
from publications.services import user_can_submit_today


EDITABLE_STATUSES = {
    Publication.Status.DRAFT,
    Publication.Status.CHANGES_REQUESTED,
}
WITHDRAWABLE_STATUSES = {
    Publication.Status.DRAFT,
    Publication.Status.PENDING_REVIEW,
    Publication.Status.CHANGES_REQUESTED,
}


def _citizen_publications(user):
    return (
        Publication.objects.filter(submitter=user)
        .select_related('category', 'category__parent')
        .prefetch_related(
            Prefetch(
                'photos',
                queryset=PublicationPhoto.objects.order_by('-is_primary', 'sort_order', 'created_at'),
                to_attr='citizen_photos',
            ),
            Prefetch(
                'moderation_decisions',
                queryset=ModerationDecision.objects.order_by('-decided_at'),
                to_attr='citizen_decisions',
            ),
        )
    )


def _publication_snapshot(publication):
    return {
        'title': publication.title,
        'description': publication.description,
        'category_id': publication.category_id,
        'status': publication.status,
        'submitted_at': publication.submitted_at.isoformat() if publication.submitted_at else None,
        'withdrawn_at': publication.withdrawn_at.isoformat() if publication.withdrawn_at else None,
    }


def _audit_publication(request, publication, action, before=None, metadata=None):
    AuditEvent.objects.create(
        actor=request.user,
        action=action,
        entity='Publication',
        entity_id=str(publication.pk),
        source='web',
        before=before,
        after=_publication_snapshot(publication),
        metadata=metadata or {},
    )


def _apply_photo_changes(publication, form):
    primary_selection = form.cleaned_data.get('primary_photo') or ''
    photos_to_remove = list(form.cleaned_data.get('remove_photos') or [])
    stored_files = [(photo.image.storage, photo.image.name) for photo in photos_to_remove]
    if photos_to_remove:
        PublicationPhoto.objects.filter(pk__in=[photo.pk for photo in photos_to_remove]).delete()
        for storage, name in stored_files:
            transaction.on_commit(lambda storage=storage, name=name: storage.delete(name))

    max_sort_order = publication.photos.aggregate(max_sort_order=Max('sort_order'))[
        'max_sort_order'
    ]
    next_sort_order = (max_sort_order if max_sort_order is not None else -1) + 1
    new_photo_objects = []
    for offset, photo in enumerate(form.cleaned_data.get('photos') or []):
        new_photo_objects.append(PublicationPhoto.objects.create(
            publication=publication,
            image=photo,
            alt_text=f'Fotografía de {publication.title}',
            sort_order=next_sort_order + offset,
            is_primary=False,
        ))

    primary_photo = None
    if primary_selection.startswith('existing:'):
        primary_photo = publication.photos.filter(
            pk=primary_selection.removeprefix('existing:')
        ).first()
    elif primary_selection.startswith('new:'):
        new_index = int(primary_selection.removeprefix('new:'))
        if new_index < len(new_photo_objects):
            primary_photo = new_photo_objects[new_index]
    if primary_photo is None:
        primary_photo = (
            publication.photos.filter(is_primary=True).first()
            or publication.photos.order_by('sort_order', 'created_at').first()
        )
    if primary_photo is not None:
        publication.photos.filter(is_primary=True).update(is_primary=False)
        primary_photo.is_primary = True
        primary_photo.save(update_fields=['is_primary'])


def _publication_form_context(form, publication):
    categories = list(
        Category.objects.filter(is_active=True)
        .order_by('sort_order', 'name')
        .values('id', 'name', 'parent_id')
    )
    return {
        'form': form,
        'publication': publication,
        'publication_categories': categories,
        'photo_max_count': settings.PUBLICATION_PHOTO_MAX_COUNT,
        'photo_max_mb': settings.PUBLICATION_PHOTO_MAX_BYTES // (1024 * 1024),
    }


def _attach_citizen_feedback(publications):
    for publication in publications:
        publication.citizen_feedback = (
            publication.citizen_decisions[0]
            if publication.status
            in {Publication.Status.CHANGES_REQUESTED, Publication.Status.REJECTED}
            and publication.citizen_decisions
            else None
        )


def publication_photo(request, photo_id):
    photo = get_object_or_404(
        PublicationPhoto.objects.select_related('publication'),
        pk=photo_id,
    )
    scoped_publication = scope_publications(
        request.user,
        Publication.objects.filter(pk=photo.publication_id),
    ).exists()
    user_can_review = request.user.is_authenticated and (
        request.user.pk == photo.publication.submitter_id
        or (user_can_moderate(request.user) and scoped_publication)
        or (
            photo.publication.status == Publication.Status.APPROVED
            and user_can_receive(request.user)
            and scoped_publication
        )
    )
    is_publicly_accessible = photo.is_publicly_accessible
    if not is_publicly_accessible and not user_can_review:
        raise Http404

    try:
        file_handle = photo.image.storage.open(photo.image.name, 'rb')
    except (FileNotFoundError, OSError):
        raise Http404 from None

    content_type = ALLOWED_IMAGE_FORMATS.get(photo.image_format, ('', 'application/octet-stream'))[1]
    response = FileResponse(
        file_handle,
        content_type=content_type,
        filename=Path(photo.image.name).name,
        as_attachment=False,
    )
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = (
        'public, max-age=3600' if is_publicly_accessible else 'private, no-store'
    )
    return response


@login_required
def publication_list(request):
    filter_form = PublicationStatusFilterForm(request.GET)
    publications = _citizen_publications(request.user)
    if filter_form.is_valid() and filter_form.cleaned_data['status']:
        publications = publications.filter(status=filter_form.cleaned_data['status'])
    elif not filter_form.is_valid():
        publications = publications.none()

    paginator = Paginator(publications.order_by('-updated_at', '-pk'), settings.PUBLICATION_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    _attach_citizen_feedback(page_obj.object_list)
    preserved_query = request.GET.copy()
    preserved_query.pop('page', None)
    return render(
        request,
        'publications/publication_list.html',
        {
            'filter_form': filter_form,
            'page_obj': page_obj,
            'paginator': paginator,
            'page_range': paginator.get_elided_page_range(page_obj.number),
            'preserved_query': preserved_query.urlencode(),
            'editable_statuses': EDITABLE_STATUSES,
            'withdrawable_statuses': WITHDRAWABLE_STATUSES,
        },
    )


@login_required
def publication_detail(request, pk):
    publication = get_object_or_404(_citizen_publications(request.user), pk=pk)
    _attach_citizen_feedback([publication])
    return render(
        request,
        'publications/publication_detail.html',
        {
            'publication': publication,
            'can_edit': publication.status in EDITABLE_STATUSES,
            'can_withdraw': publication.status in WITHDRAWABLE_STATUSES,
        },
    )


@login_required
def publication_create(request):
    action = request.POST.get('action') if request.method == 'POST' else None
    form = CitizenPublicationForm(
        request.POST or None,
        request.FILES or None,
        require_photo=action == 'submit',
    )
    if request.method == 'POST':
        form_is_valid = form.is_valid()
        if action not in {'save_draft', 'submit'}:
            form.add_error(None, 'Selecciona una acción válida.')
        elif form_is_valid:
            if action == 'submit':
                can_submit, daily_limit = user_can_submit_today(request.user)
                if not can_submit:
                    form.add_error(
                        None,
                        f'Has alcanzado el límite de {daily_limit} publicaciones enviadas hoy.',
                    )
            if not form.errors:
                with transaction.atomic():
                    publication = form.save(commit=False)
                    publication.submitter = request.user
                    publication.status_changed_at = timezone.now()
                    if action == 'submit':
                        publication.status = Publication.Status.PENDING_REVIEW
                        publication.submitted_at = timezone.now()
                    else:
                        publication.status = Publication.Status.DRAFT
                    publication.save()
                    _apply_photo_changes(publication, form)
                    _audit_publication(
                        request,
                        publication,
                        'publication.submitted' if action == 'submit' else 'publication.draft_created',
                    )
                if action == 'submit':
                    messages.success(
                        request,
                        'Tu objeto se ha enviado a revisión. Te avisaremos si necesitamos algún cambio.',
                    )
                else:
                    messages.success(request, 'El borrador se ha guardado.')
                return redirect('publications:detail', pk=publication.pk)

    return render(
        request,
        'publications/publication_form.html',
        _publication_form_context(form, None),
    )


@login_required
def publication_edit(request, pk):
    publication = get_object_or_404(Publication, pk=pk, submitter=request.user)
    if publication.status not in EDITABLE_STATUSES:
        raise PermissionDenied
    before = _publication_snapshot(publication)
    action = request.POST.get('action') if request.method == 'POST' else None
    form = CitizenPublicationForm(
        request.POST or None,
        request.FILES or None,
        instance=publication,
        require_photo=action == 'submit',
    )
    if request.method == 'POST':
        form_is_valid = form.is_valid()
        if action not in {'save_draft', 'submit'}:
            form.add_error(None, 'Selecciona una acción válida.')
        elif form_is_valid:
            if action == 'submit':
                can_submit, daily_limit = user_can_submit_today(request.user)
                if not can_submit:
                    form.add_error(
                        None,
                        f'Has alcanzado el límite de {daily_limit} publicaciones enviadas hoy.',
                    )
            if not form.errors:
                with transaction.atomic():
                    publication = form.save(commit=False)
                    publication.status_changed_at = timezone.now()
                    if action == 'submit':
                        publication.status = Publication.Status.PENDING_REVIEW
                        publication.submitted_at = timezone.now()
                        publication.reviewed_at = None
                    publication.save()
                    _apply_photo_changes(publication, form)
                    _audit_publication(
                        request,
                        publication,
                        'publication.submitted' if action == 'submit' else 'publication.updated',
                        before=before,
                    )
                messages.success(
                    request,
                    'La publicación se ha enviado de nuevo a revisión.'
                    if action == 'submit'
                    else 'Los cambios se han guardado.',
                )
                return redirect('publications:detail', pk=publication.pk)

    return render(
        request,
        'publications/publication_form.html',
        _publication_form_context(form, publication),
    )


@login_required
def publication_withdraw(request, pk):
    publication = get_object_or_404(Publication, pk=pk, submitter=request.user)
    if publication.status not in WITHDRAWABLE_STATUSES:
        raise PermissionDenied
    form = PublicationWithdrawalForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        before = _publication_snapshot(publication)
        with transaction.atomic():
            publication.status = Publication.Status.WITHDRAWN
            publication.withdrawn_at = timezone.now()
            publication.status_changed_at = publication.withdrawn_at
            publication.save(update_fields=['status', 'withdrawn_at', 'status_changed_at', 'updated_at'])
            _audit_publication(
                request,
                publication,
                'publication.withdrawn',
                before=before,
                metadata={'reason': form.cleaned_data['reason']},
            )
        messages.success(request, 'La publicación se ha retirado.')
        return redirect('publications:detail', pk=publication.pk)

    return render(
        request,
        'publications/publication_withdraw.html',
        {'form': form, 'publication': publication},
    )
