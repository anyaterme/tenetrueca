from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render

from audit.models import AuditEvent
from core.permissions import user_can_moderate
from moderation.forms import ModerationDecisionForm, ModerationQueueFilterForm
from moderation.models import ModerationDecision
from moderation.services import moderate_publication
from publications.models import Publication, PublicationPhoto


REVIEWABLE_STATUSES = {
    Publication.Status.PENDING_REVIEW,
    Publication.Status.CHANGES_REQUESTED,
    Publication.Status.APPROVED,
    Publication.Status.REJECTED,
}


def _require_moderator(user):
    if not user_can_moderate(user):
        raise PermissionDenied


def _staff_publications():
    return (
        Publication.objects.filter(
            submitted_at__isnull=False,
            status__in=REVIEWABLE_STATUSES,
        )
        .select_related(
            'submitter',
            'submitter__habitual_recycling_center',
            'category',
            'category__parent',
        )
        .prefetch_related(
            Prefetch(
                'photos',
                queryset=PublicationPhoto.objects.order_by(
                    '-is_primary',
                    'sort_order',
                    'created_at',
                ),
                to_attr='staff_photos',
            ),
            Prefetch(
                'moderation_decisions',
                queryset=ModerationDecision.objects.select_related('reviewer').order_by(
                    '-decided_at'
                ),
                to_attr='staff_decisions',
            ),
        )
    )


@login_required
def moderation_queue(request):
    _require_moderator(request.user)
    filter_data = request.GET.copy()
    if not filter_data:
        filter_data.update(
            {'status': Publication.Status.PENDING_REVIEW, 'ordering': 'oldest'}
        )
    filter_form = ModerationQueueFilterForm(filter_data)
    publications = _staff_publications()

    if filter_form.is_valid():
        status = filter_form.cleaned_data['status']
        center = filter_form.cleaned_data['center']
        date_from = filter_form.cleaned_data['date_from']
        date_to = filter_form.cleaned_data['date_to']
        ordering = filter_form.cleaned_data['ordering'] or 'oldest'
        if status:
            publications = publications.filter(status=status)
        if center:
            publications = publications.filter(submitter__habitual_recycling_center=center)
        if date_from:
            publications = publications.filter(submitted_at__date__gte=date_from)
        if date_to:
            publications = publications.filter(submitted_at__date__lte=date_to)
        publications = publications.order_by(
            '-submitted_at' if ordering == 'newest' else 'submitted_at',
            '-pk' if ordering == 'newest' else 'pk',
        )
    else:
        publications = publications.none()

    paginator = Paginator(publications, settings.MODERATION_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    preserved_query = request.GET.copy()
    preserved_query.pop('page', None)
    return render(
        request,
        'moderation/queue.html',
        {
            'filter_form': filter_form,
            'page_obj': page_obj,
            'paginator': paginator,
            'page_range': paginator.get_elided_page_range(page_obj.number),
            'preserved_query': preserved_query.urlencode(),
            'staff_section': 'moderation',
        },
    )


def _review_context(publication, decision_form):
    audit_events = AuditEvent.objects.filter(
        entity='Publication',
        entity_id=str(publication.pk),
    ).select_related('actor')[:20]
    return {
        'publication': publication,
        'decision_form': decision_form,
        'can_decide': publication.status == Publication.Status.PENDING_REVIEW,
        'audit_events': audit_events,
        'staff_section': 'moderation',
    }


@login_required
def moderation_detail(request, pk):
    _require_moderator(request.user)
    publication = get_object_or_404(_staff_publications(), pk=pk)
    return render(
        request,
        'moderation/detail.html',
        _review_context(publication, ModerationDecisionForm()),
    )


@login_required
def moderation_decide(request, pk):
    _require_moderator(request.user)
    if request.method != 'POST':
        return redirect('moderation:detail', pk=pk)
    publication = get_object_or_404(_staff_publications(), pk=pk)
    form = ModerationDecisionForm(request.POST)
    if form.is_valid():
        try:
            publication, _ = moderate_publication(
                publication_id=publication.pk,
                reviewer=request.user,
                decision=form.cleaned_data['decision'],
                notes=form.cleaned_data['notes'],
            )
        except ValidationError as error:
            form.add_error(None, error.message)
        else:
            messages.success(
                request,
                f'Decisión registrada: {publication.get_status_display()}.',
            )
            return redirect('moderation:detail', pk=publication.pk)
    return render(request, 'moderation/detail.html', _review_context(publication, form))
