from django.urls import path

from catalog.views import catalog_detail, catalog_list


app_name = 'catalog'

urlpatterns = [
    path('', catalog_list, name='list'),
    path('<str:reference>/', catalog_detail, name='detail'),
]
