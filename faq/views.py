from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from faq.forms import FAQDeleteForm, FAQForm
from audit.models import AuditEvent
from faq.audit import FAQ_ACTIONS, faq_snapshot, history_item, record_faq_event
from faq.models import FAQ
from faq.permissions import faq_permission_required, user_can_manage_faq


@require_GET
def public_list(request):
    return render(request, 'faq/public_list.html', {
        'faqs': FAQ.objects.filter(is_published=True),
    })


@login_required
@faq_permission_required('view')
@require_GET
def manage_list(request):
    return render(request, 'faq/manage_list.html', {
        'faqs': FAQ.objects.all(),
        'staff_section': 'faq',
        'can_add_faq': user_can_manage_faq(request.user, 'add'),
        'can_change_faq': user_can_manage_faq(request.user, 'change'),
        'can_delete_faq': user_can_manage_faq(request.user, 'delete'),
    })


def _save_form(request, instance=None):
    form = FAQForm(request.POST if request.method == 'POST' else None, instance=instance)
    if request.method == 'POST':
        if form.is_valid():
            with transaction.atomic():
                before = faq_snapshot(get_object_or_404(FAQ.objects.select_for_update(), pk=instance.pk)) if instance else None
                after = faq_snapshot(form.instance)
                if before != after:
                    saved = form.save()
                    record_faq_event(
                        actor=request.user, faq_id=saved.pk,
                        action='faq.updated' if instance else 'faq.created',
                        before=before, after=faq_snapshot(saved),
                    )
                else:
                    messages.info(request, 'No hay cambios que guardar.')
                    return redirect('faq:manage_list')
            messages.success(request, 'Pregunta actualizada.' if instance else 'Pregunta creada.')
            return redirect('faq:manage_list')
        messages.error(request, 'Revisa los campos indicados para guardar la pregunta.')
    return render(request, 'faq/form.html', {
        'form': form, 'faq': instance, 'staff_section': 'faq',
    })


@login_required
@faq_permission_required('add')
@require_http_methods(['GET', 'POST'])
def create(request):
    return _save_form(request)


@login_required
@faq_permission_required('change')
@require_http_methods(['GET', 'POST'])
def edit(request, pk):
    return _save_form(request, get_object_or_404(FAQ, pk=pk))


@login_required
@faq_permission_required('delete')
@require_GET
def delete_confirm(request, pk):
    return render(request, 'faq/delete_confirm.html', {
        'faq': get_object_or_404(FAQ, pk=pk),
        'form': FAQDeleteForm(), 'staff_section': 'faq',
    })


@login_required
@faq_permission_required('delete')
@require_POST
def delete(request, pk):
    faq = get_object_or_404(FAQ, pk=pk)
    form = FAQDeleteForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Confirma la eliminación de la pregunta.')
        return render(request, 'faq/delete_confirm.html', {
            'faq': faq, 'form': form, 'staff_section': 'faq',
        }, status=400)
    with transaction.atomic():
        faq = get_object_or_404(FAQ.objects.select_for_update(), pk=pk)
        before = faq_snapshot(faq)
        faq.delete()
        record_faq_event(actor=request.user, faq_id=pk, action='faq.deleted', before=before)
    messages.success(request, 'Pregunta eliminada.')
    return redirect('faq:manage_list')


@login_required
@faq_permission_required('view')
@require_GET
def history(request, pk=None):
    events = AuditEvent.objects.filter(entity='FAQ', action__in=FAQ_ACTIONS).select_related('actor').order_by('-created_at', '-pk')
    if pk is not None:
        events = events.filter(entity_id=str(pk))
    action = request.GET.get('action', '')
    if action in FAQ_ACTIONS:
        events = events.filter(action=action)
    else:
        action = ''
    paginator = Paginator(events, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'faq/history.html', {
        'history_items': [history_item(event) for event in page_obj],
        'page_obj': page_obj, 'paginator': paginator,
        'page_range': paginator.get_elided_page_range(page_obj.number),
        'preserved_query': query.urlencode(), 'actions': FAQ_ACTIONS,
        'selected_action': action, 'faq_id': pk, 'staff_section': 'faq',
    })
