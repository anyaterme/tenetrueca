from django.urls import path

from publications.views import publication_photo


urlpatterns = [
    path('<int:photo_id>/', publication_photo, name='publication_photo'),
]
