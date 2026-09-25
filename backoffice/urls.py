from django.urls import path
from django.views.generic import RedirectView

from backoffice import views


app_name = 'backoffice'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('sin-asignacion/', views.no_assignment, name='no_assignment'),
    path('recogidas/', views.pickup_queue, name='pickup_queue'),
    path('usuarios/', views.user_list, name='user_list'),
    path('equipo/', views.team_list, name='team_list'),
    path('equipo/nuevo/', views.team_add, name='team_add'),
    path(
        'equipo/conceder-acceso/',
        views.team_grant_existing,
        name='team_grant_existing',
    ),
    path('equipo/<int:pk>/', views.team_detail, name='team_detail'),
    path(
        'equipo/<int:pk>/desactivar/',
        views.team_deactivate,
        name='team_deactivate',
    ),
    path(
        'equipo/<int:pk>/reactivar/',
        views.team_reactivate,
        name='team_reactivate',
    ),
    path(
        'equipo/<int:pk>/retirar-acceso/',
        views.team_remove,
        name='team_remove',
    ),
    path(
        'invitacion/<str:token>/',
        views.staff_invitation_accept,
        name='staff_invitation_accept',
    ),
    path(
        'operadores/',
        RedirectView.as_view(pattern_name='backoffice:team_list', permanent=False),
        name='operator_list',
    ),
]
