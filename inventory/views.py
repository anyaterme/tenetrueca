from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render

from core.permissions import allowed_reception_centers, scope_publications
from inventory.forms import ReceptionInspectionForm, ReceptionQueueFilterForm
from inventory.models import ReceptionInspection
from inventory.services import inspect_reception
from publications.models import Publication, PublicationPhoto


def _require_reception_access(user):
    centers = allowed_reception_centers(user)
    if not centers.exists():
        raise PermissionDenied
    return centers


def _approved_publications():
    return (
        Publication.objects.filter(status=Publication.Status.APPROVED)
        .select_related('submitter', 'category', 'category__parent')
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
                'reception_inspections',
                queryset=ReceptionInspection.objects.select_related(
                    'center',
                    'operator',
                    'inventory_item',
                ).order_by('-inspected_at'),
                to_attr='staff_inspections',
            ),
        )
    )


@login_required
def reception_queue(request):
    centers = _require_reception_access(request.user)
    filter_data = request.GET.copy()
    if not filter_data:
        filter_data.update({'ordering': 'oldest'})
    filter_form = ReceptionQueueFilterForm(filter_data)
    publications = scope_publications(
        request.user,
        _approved_publications().filter(inventory_object__isnull=True),
    )
    if filter_form.is_valid():
        query = filter_form.cleaned_data['q']
        ordering = filter_form.cleaned_data['ordering'] or 'oldest'
        if query:
            publications = publications.filter(
                Q(title__icontains=query)
                | Q(submitter__email__icontains=query)
                | Q(submitter__username__icontains=query)
            )
        publications = publications.order_by(
            '-approved_at' if ordering == 'newest' else 'approved_at',
            '-pk' if ordering == 'newest' else 'pk',
        )
    else:
        publications = publications.none()

    paginator = Paginator(publications, settings.RECEPTION_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    preserved_query = request.GET.copy()
    preserved_query.pop('page', None)
    return render(
        request,
        'inventory/reception_queue.html',
        {
            'filter_form': filter_form,
            'page_obj': page_obj,
            'paginator': paginator,
            'page_range': paginator.get_elided_page_range(page_obj.number),
            'preserved_query': preserved_query.urlencode(),
            'allowed_centers': centers,
            'staff_section': 'reception',
        },
    )


@login_required
def reception_detail(request, pk):
    centers = _require_reception_access(request.user)
    publication = get_object_or_404(
        scope_publications(request.user, _approved_publications()),
        pk=pk,
    )
    accepted_inspection = next(
        (
            inspection
            for inspection in publication.staff_inspections
            if inspection.decision == ReceptionInspection.Decision.ACCEPT
        ),
        None,
    )
    try:
        accepted_item = publication.inventory_object
    except ObjectDoesNotExist:
        accepted_item = None
    if accepted_inspection is not None:
        accepted_item = accepted_inspection.inventory_item
    if (
        accepted_item is not None
        and not centers.filter(pk=accepted_item.center_id).exists()
    ):
        raise PermissionDenied
    form = ReceptionInspectionForm(request.POST or None, centers=centers)

    if request.method == 'POST' and accepted_item is None and form.is_valid():
        try:
            item, inspection, created = inspect_reception(
                publication_id=publication.pk,
                operator=request.user,
                center=form.cleaned_data['center'],
                decision=form.cleaned_data['decision'],
                condition=form.cleaned_data['condition'],
                internal_location=form.cleaned_data['internal_location'],
                notes=form.cleaned_data['notes'],
            )
        except ValidationError as error:
            for message in error.messages:
                form.add_error(None, message)
        else:
            if inspection.decision == ReceptionInspection.Decision.ACCEPT:
                messages.success(
                    request,
                    'Recepción aceptada. El objeto ya está disponible en el catálogo.',
                )
            else:
                messages.success(request, 'Recepción rechazada y registrada en el historial.')
            return redirect('inventory:reception_detail', pk=publication.pk)

    return render(
        request,
        'inventory/reception_detail.html',
        {
            'publication': publication,
            'form': form,
            'accepted_inspection': accepted_inspection,
            'accepted_item': accepted_item,
            'staff_section': 'reception',
        },
    )
