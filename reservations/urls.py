from django.urls import path

from reservations import views


app_name = 'reservations'

urlpatterns = [
    path('', views.reservation_list, name='list'),
    path('crear/<str:reference>/', views.create_reservation_view, name='create'),
    path('<uuid:public_id>/', views.reservation_detail, name='detail'),
    path('<uuid:public_id>/cancelar/', views.cancel_reservation_view, name='cancel'),
    path('operaciones/qr/<path:token>/', views.pickup_scan, name='pickup_scan'),
]
