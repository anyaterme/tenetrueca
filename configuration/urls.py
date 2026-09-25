from django.urls import path

from configuration import views


app_name = 'configuration'

urlpatterns = [
    path('correo-electronico/', views.email_configuration, name='email'),
    path(
        'correo-electronico/restablecer/',
        views.reset_email_configuration,
        name='email_reset',
    ),
    path(
        'correo-electronico/probar/',
        views.test_email_configuration,
        name='email_test',
    ),
]
