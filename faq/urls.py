from django.urls import path

from faq import views


app_name = 'faq'

urlpatterns = [
    path('faq/', views.public_list, name='public_list'),
    path('backoffice/faq/', views.manage_list, name='manage_list'),
    path('backoffice/faq/historial/', views.history, name='history'),
    path('backoffice/faq/<int:pk>/historial/', views.history, name='question_history'),
    path('backoffice/faq/nueva/', views.create, name='create'),
    path('backoffice/faq/<int:pk>/editar/', views.edit, name='edit'),
    path('backoffice/faq/<int:pk>/eliminar/', views.delete_confirm, name='delete_confirm'),
    path('backoffice/faq/<int:pk>/eliminar/confirmar/', views.delete, name='delete'),
]
