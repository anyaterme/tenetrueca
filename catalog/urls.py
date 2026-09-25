from django.urls import path

from catalog.views import (
    catalog_detail,
    catalog_list,
    favorite_add,
    favorite_list,
    favorite_remove,
)


app_name = 'catalog'

urlpatterns = [
    path('', catalog_list, name='list'),
    path('favoritos/', favorite_list, name='favorites'),
    path('favoritos/agregar/<str:reference>/', favorite_add, name='favorite_add'),
    path('favoritos/quitar/<str:reference>/', favorite_remove, name='favorite_remove'),
    path('<str:reference>/', catalog_detail, name='detail'),
]
