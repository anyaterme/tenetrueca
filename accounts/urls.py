from django.contrib.auth import views as auth_views
from django.urls import path

from accounts.views import (
    AccountLoginView,
    AccountPasswordChangeView,
    AccountPasswordResetConfirmView,
    AccountPasswordResetView,
    DashboardView,
    MagicLinkConsumeView,
    MagicLinkRequestedView,
    MagicLinkRequestView,
    PreferencesView,
    ProfileUpdateView,
    ProfileView,
    RegisterView,
)


urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('login/', AccountLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('enlace-acceso/', MagicLinkRequestView.as_view(), name='magic-login-request'),
    path(
        'enlace-acceso/enviado/',
        MagicLinkRequestedView.as_view(),
        name='magic-login-requested',
    ),
    path('acceso/<str:token>/', MagicLinkConsumeView.as_view(), name='magic-login-consume'),
    path('registro/', RegisterView.as_view(), name='register'),
    path('recuperar-password/', AccountPasswordResetView.as_view(), name='password_reset'),
    path(
        'recuperar-password/enviado/',
        auth_views.PasswordResetDoneView.as_view(template_name='accounts/password_reset_done.html'),
        name='password_reset_done',
    ),
    path(
        'recuperar-password/<uidb64>/<token>/',
        AccountPasswordResetConfirmView.as_view(),
        name='password_reset_confirm',
    ),
    path(
        'recuperar-password/completado/',
        auth_views.PasswordResetCompleteView.as_view(template_name='accounts/password_reset_complete.html'),
        name='password_reset_complete',
    ),
    path('perfil/', ProfileView.as_view(), name='profile'),
    path('perfil/editar/', ProfileUpdateView.as_view(), name='profile-edit'),
    path('cambiar-password/', AccountPasswordChangeView.as_view(), name='password-change'),
    path('preferencias/', PreferencesView.as_view(), name='preferences'),
]
