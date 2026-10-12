from django.urls import path
from locations import views

app_name = 'locations'
urlpatterns = [
    path('', views.manage_list, name='manage_list'),
    path('nuevo/', views.create, name='create'),
    path('<int:pk>/editar/', views.edit, name='edit'),
]
