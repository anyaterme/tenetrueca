from django.urls import path

from publications import views


app_name = 'publications'

urlpatterns = [
    path('', views.publication_list, name='list'),
    path('nueva/', views.publication_create, name='create'),
    path('<int:pk>/', views.publication_detail, name='detail'),
    path('<int:pk>/editar/', views.publication_edit, name='edit'),
    path('<int:pk>/retirar/', views.publication_withdraw, name='withdraw'),
]
