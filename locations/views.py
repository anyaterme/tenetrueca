from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from audit.models import AuditEvent
from locations.forms import RecyclingCenterForm
from locations.models import RecyclingCenter
from locations.permissions import center_permission_required, user_can_manage_centers
from locations.services import maps_configuration


@login_required
@center_permission_required('view')
@require_GET
def manage_list(request):
    centers = RecyclingCenter.objects.all()
    query = request.GET.get('q', '').strip()
    if query:
        centers = centers.filter(Q(name__icontains=query) | Q(address__icontains=query))
    return render(request, 'locations/manage_list.html', {
        'centers': centers, 'query': query, 'staff_section': 'centers',
        'can_add_center': user_can_manage_centers(request.user, 'add'),
        'can_change_center': user_can_manage_centers(request.user, 'change'),
    })


def _snapshot(center):
    return {name: str(getattr(center, name)) if name in ('latitude', 'longitude') and getattr(center, name) is not None else getattr(center, name)
            for name in RecyclingCenterForm.Meta.fields}


def _save_form(request, center=None):
    before = _snapshot(center) if center else None
    form = RecyclingCenterForm(request.POST if request.method == 'POST' else None, instance=center)
    if request.method == 'POST':
        if form.is_valid():
            with transaction.atomic():
                saved = form.save()
                AuditEvent.objects.create(
                    actor=request.user, entity='RecyclingCenter', entity_id=str(saved.pk),
                    action='center.updated' if center else 'center.created', source='backoffice',
                    before=before, after=_snapshot(saved),
                )
            messages.success(request, 'Punto limpio actualizado.' if center else 'Punto limpio creado.')
            return redirect('locations:manage_list')
        messages.error(request, 'Revisa los campos indicados para guardar el punto limpio.')
    return render(request, 'locations/form.html', {
        'form': form, 'center': center, 'staff_section': 'centers', 'maps_config': maps_configuration(),
    })


@login_required
@center_permission_required('add')
@require_http_methods(['GET', 'POST'])
def create(request):
    return _save_form(request)


@login_required
@center_permission_required('change')
@require_http_methods(['GET', 'POST'])
def edit(request, pk):
    return _save_form(request, get_object_or_404(RecyclingCenter, pk=pk))
