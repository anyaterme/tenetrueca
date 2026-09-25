from django.urls import path

from inventory import views


app_name = 'inventory'

urlpatterns = [
    path('', views.reception_queue, name='reception_queue'),
    path('<int:pk>/', views.reception_detail, name='reception_detail'),
]
