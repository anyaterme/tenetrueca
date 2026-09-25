from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from django.utils.functional import cached_property


@deconstructible
class PrivatePublicationPhotoStorage(FileSystemStorage):
    @cached_property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @cached_property
    def base_url(self):
        return None


private_publication_photo_storage = PrivatePublicationPhotoStorage()
