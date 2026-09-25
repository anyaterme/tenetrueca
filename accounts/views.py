from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, PasswordChangeView, PasswordResetConfirmView, PasswordResetView
from django.db import models, transaction
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
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
from core.permissions import user_can_access_backoffice
from points.services import award_registration_points, points_balance
from publications.models import Publication, PublicationPhoto
from reservations.models import Reservation


def is_operations_staff(user):
    return user_can_access_backoffice(user)


def authenticated_home_url(user):
    if user.must_change_password:
        return reverse('password-change')
    return reverse('backoffice:dashboard' if is_operations_staff(user) else 'dashboard')


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

    def get_default_redirect_url(self):
        return authenticated_home_url(self.request.user)


class MagicLinkRequestView(MagicLoginEnabledMixin, FormView):
    form_class = MagicLinkRequestForm
    template_name = 'accounts/magic_login_request.html'
    success_url = reverse_lazy('magic-login-requested')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(authenticated_home_url(request.user))
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
        return redirect(consumed.redirect_path or authenticated_home_url(consumed.user))


class RegisterView(LocalAuthenticationOnlyMixin, FormView):
    form_class = RegistrationForm
    template_name = 'accounts/register.html'
    success_url = reverse_lazy('dashboard')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(authenticated_home_url(request.user))
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save()
            award_registration_points(user=user)
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

    def form_valid(self, form):
        response = super().form_valid(form)
        self.user.must_change_password = False
        self.user.save(update_fields=['must_change_password'])
        return response


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = 'accounts/profile.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['points_balance'] = points_balance(self.request.user)
        return context


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'accounts/dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        if is_operations_staff(request.user):
            return redirect('backoffice:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        publications = (
            Publication.objects.filter(submitter=user)
            .select_related('category')
            .prefetch_related(
                models.Prefetch(
                    'photos',
                    queryset=PublicationPhoto.objects.order_by(
                        '-is_primary', 'sort_order', 'created_at'
                    ),
                    to_attr='dashboard_photos',
                )
            )
            .order_by('-updated_at', '-pk')
        )
        reservations = (
            Reservation.objects.filter(user=user)
            .select_related('inventory_item', 'inventory_item__center')
            .order_by('-reserved_at', '-pk')
        )
        context.update(
            {
                'dashboard_name': user.first_name or user.username or user.email,
                'points_balance': points_balance(user),
                'publication_count': publications.count(),
                'latest_publication': publications.first(),
                'active_reservation_count': reservations.filter(
                    status=Reservation.Status.ACTIVE
                ).count(),
                'latest_reservation': reservations.first(),
                'favorite_count': user.favorites.count(),
            }
        )
        return context


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

    def get_template_names(self):
        if self.request.user.must_change_password:
            return ['accounts/password_change_required.html']
        return super().get_template_names()

    def get_success_url(self):
        if self.request.user.must_change_password:
            return reverse(
                'backoffice:dashboard'
                if is_operations_staff(self.request.user)
                else 'dashboard'
            )
        return super().get_success_url()

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
        password_change_was_required = self.request.user.must_change_password
        response = super().form_valid(form)
        if password_change_was_required:
            self.request.user.must_change_password = False
            self.request.user.save(update_fields=['must_change_password'])
        messages.success(self.request, 'Tu contraseña se ha actualizado.')
        return response
