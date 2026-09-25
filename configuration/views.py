from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from configuration.forms import EmailConfigurationForm, EmailTestForm
from configuration.permissions import email_configuration_required
from configuration.services import EmailConfigurationService


FEEDBACK_SESSION_KEY = 'email_configuration_feedback'


def _set_feedback(request, *, icon, title, text):
    request.session[FEEDBACK_SESSION_KEY] = {
        'icon': icon,
        'title': title,
        'text': text,
    }


def _page_context(request, *, form=None, test_form=None):
    effective = EmailConfigurationService.effective()
    override = EmailConfigurationService.get_override()
    return {
        'staff_section': 'configuration',
        'form': form
        or EmailConfigurationForm(override=override, effective=effective),
        'test_form': test_form
        or EmailTestForm(initial={'recipient': request.user.email}),
        'field_sources': effective.sources,
        'password_override_exists': bool(
            override and override.email_host_password_encrypted is not None
        ),
        'has_email_overrides': override is not None,
        'configuration_feedback': request.session.pop(
            FEEDBACK_SESSION_KEY,
            None,
        ),
    }


@never_cache
@login_required
@email_configuration_required
def email_configuration(request):
    if request.method == 'POST':
        effective = EmailConfigurationService.effective()
        override = EmailConfigurationService.get_override()
        form = EmailConfigurationForm(
            request.POST,
            override=override,
            effective=effective,
        )
        if form.is_valid():
            EmailConfigurationService.save_overrides(
                actor=request.user,
                overrides=form.overrides,
                password_action=form.cleaned_data['password_action'],
                new_password=form.cleaned_data.get('new_password', ''),
            )
            _set_feedback(
                request,
                icon='success',
                title='Configuración guardada',
                text='Los próximos envíos utilizarán la configuración efectiva actualizada.',
            )
            return redirect('configuration:email')
        return render(
            request,
            'configuration/email.html',
            _page_context(request, form=form),
            status=400,
        )

    return render(
        request,
        'configuration/email.html',
        _page_context(request),
    )


@require_POST
@login_required
@email_configuration_required
def reset_email_configuration(request):
    removed = EmailConfigurationService.reset(actor=request.user)
    _set_feedback(
        request,
        icon='success',
        title='Valores restablecidos',
        text=(
            'Se han eliminado los overrides y se vuelve a utilizar la configuración del servidor.'
            if removed
            else 'No había overrides de correo almacenados.'
        ),
    )
    return redirect('configuration:email')


@require_POST
@login_required
@email_configuration_required
def test_email_configuration(request):
    form = EmailTestForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            'configuration/email.html',
            _page_context(request, test_form=form),
            status=400,
        )
    try:
        EmailConfigurationService.send_test(
            recipient=form.cleaned_data['recipient'],
            actor=request.user,
        )
    except Exception as error:
        _set_feedback(
            request,
            icon='error',
            title='No se pudo enviar el correo',
            text=EmailConfigurationService.public_error_message(error),
        )
    else:
        _set_feedback(
            request,
            icon='success',
            title='Correo enviado correctamente',
            text='La configuración SMTP ha respondido correctamente.',
        )
    return redirect('configuration:email')
