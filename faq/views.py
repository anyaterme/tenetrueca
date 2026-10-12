from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from faq.forms import FAQDeleteForm, FAQForm
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
            form.save()
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
    faq.delete()
    messages.success(request, 'Pregunta eliminada.')
    return redirect('faq:manage_list')
