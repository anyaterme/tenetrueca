from django.urls import path

from points.views import movement_list


app_name = 'points'

urlpatterns = [
    path('', movement_list, name='movements'),
]
