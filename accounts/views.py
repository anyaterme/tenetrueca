from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, PasswordChangeView, PasswordResetConfirmView, PasswordResetView
from django.shortcuts import redirect, render, resolve_url
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import FormView, TemplateView

from accounts.forms import (
    EmailAuthenticationForm,
    MagicLinkRequestForm,
    PreferencesForm,
    ProfileUpdateForm,
    RegistrationForm,
)
from accounts.services import MagicLinkService
from audit.models import AuditEvent


class LocalAuthenticationOnlyMixin:
    def dispatch(self, request, *args, **kwargs):
        if settings.AUTH_PROVIDER != 'local':
            return render(request, 'accounts/auth_action_unavailable.html', status=403)
        return super().dispatch(request, *args, **kwargs)


class MagicLoginEnabledMixin:
    def dispatch(self, request, *args, **kwargs):
        if not settings.MAGIC_LOGIN_ENABLED:
            return render(request, 'accounts/auth_action_unavailable.html', status=403)
        return super().dispatch(request, *args, **kwargs)


class AccountLoginView(LoginView):
    authentication_form = EmailAuthenticationForm
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True


class MagicLinkRequestView(MagicLoginEnabledMixin, FormView):
    form_class = MagicLinkRequestForm
    template_name = 'accounts/magic_login_request.html'
    success_url = reverse_lazy('magic-login-requested')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('profile')
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        initial['next'] = self.request.GET.get('next', '')
        return initial

    def form_valid(self, form):
        MagicLinkService.request_link(
            request=self.request,
            identifier=form.cleaned_data['identifier'],
            redirect_path=self.request.POST.get('next', ''),
        )
        return super().form_valid(form)


class MagicLinkRequestedView(MagicLoginEnabledMixin, TemplateView):
    template_name = 'accounts/magic_login_requested.html'


class MagicLinkConsumeView(MagicLoginEnabledMixin, View):
    def get(self, request, token):
        consumed = MagicLinkService.consume(token)
        if consumed is None:
            return render(request, 'accounts/magic_login_invalid.html', status=400)

        login(
            request,
            consumed.user,
            backend=settings.AUTHENTICATION_BACKENDS[0],
        )
        AuditEvent.objects.create(
            actor=consumed.user,
            action='magic_login.used',
            entity='MagicLoginToken',
            entity_id=str(consumed.record.pk),
            source='web',
            result='success',
            metadata={'redirected': bool(consumed.redirect_path)},
        )
        return redirect(consumed.redirect_path or resolve_url(settings.LOGIN_REDIRECT_URL))


class RegisterView(LocalAuthenticationOnlyMixin, FormView):
    form_class = RegistrationForm
    template_name = 'accounts/register.html'
    success_url = reverse_lazy('profile')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('profile')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        authenticated_user = authenticate(
            self.request,
            username=user.email,
            password=form.cleaned_data['password1'],
        )
        if authenticated_user is None:
            form.add_error(None, 'No se pudo iniciar la sesión. Inténtalo de nuevo.')
            return self.form_invalid(form)
        login(self.request, authenticated_user)
        messages.success(self.request, 'Tu cuenta se ha creado correctamente.')
        return super().form_valid(form)


class AccountPasswordResetView(LocalAuthenticationOnlyMixin, PasswordResetView):
    template_name = 'accounts/password_reset_form.html'
    email_template_name = 'accounts/password_reset_email.html'
    subject_template_name = 'accounts/password_reset_subject.txt'
    success_url = reverse_lazy('password_reset_done')


class AccountPasswordResetConfirmView(LocalAuthenticationOnlyMixin, PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = 'accounts/profile.html'


class ProfileUpdateView(LoginRequiredMixin, FormView):
    form_class = ProfileUpdateForm
    template_name = 'accounts/profile_edit.html'
    success_url = reverse_lazy('profile')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.success(self.request, 'Tu perfil se ha actualizado.')
        return super().form_valid(form)


class PreferencesView(LoginRequiredMixin, FormView):
    form_class = PreferencesForm
    template_name = 'accounts/preferences.html'
    success_url = reverse_lazy('preferences')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.success(self.request, 'Tus preferencias se han guardado.')
        return super().form_valid(form)


class AccountPasswordChangeView(
    LoginRequiredMixin,
    PasswordChangeView,
):
    template_name = 'accounts/password_change.html'
    success_url = reverse_lazy('profile')

    def password_is_managed_externally(self):
        return (
            settings.AUTH_PROVIDER != 'local'
            or self.request.user.auth_source != self.request.user.AuthSource.LOCAL
        )

    def get_external_password_management_url(self):
        return ''

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['password_managed_externally'] = self.password_is_managed_externally()
        context['external_password_management_url'] = (
            self.get_external_password_management_url()
        )
        return context

    def post(self, request, *args, **kwargs):
        if self.password_is_managed_externally():
            return render(
                request,
                self.template_name,
                self.get_context_data(),
                status=403,
            )
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, 'Tu contraseña se ha actualizado.')
        return super().form_valid(form)
