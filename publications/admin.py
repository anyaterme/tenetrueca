from django.conf import settings
from django.contrib import admin

from publications.forms import PublicationAdminForm, PublicationPhotoForm
from publications.models import Publication, PublicationPhoto


class PublicationPhotoInline(admin.TabularInline):
    model = PublicationPhoto
    form = PublicationPhotoForm
    extra = 0
    max_num = settings.PUBLICATION_PHOTO_MAX_COUNT
    fields = ('image', 'alt_text', 'sort_order', 'is_primary')


@admin.register(Publication)
class PublicationAdmin(admin.ModelAdmin):
    form = PublicationAdminForm
    inlines = (PublicationPhotoInline,)
    list_display = ('title', 'submitter', 'category', 'status', 'submitted_at', 'created_at')
    list_filter = ('status', 'category')
    search_fields = ('title', 'description', 'submitter__email', 'submitter__username')
    autocomplete_fields = ('submitter', 'category')
    filter_horizontal = ('tags',)
    readonly_fields = ('submitted_at', 'reviewed_at', 'approved_at', 'withdrawn_at')


@admin.register(PublicationPhoto)
class PublicationPhotoAdmin(admin.ModelAdmin):
    form = PublicationPhotoForm
    list_display = ('publication', 'sort_order', 'is_primary', 'image_format', 'width', 'height')
    list_filter = ('is_primary', 'image_format')
    search_fields = ('publication__title', 'publication__submitter__email', 'alt_text')
    autocomplete_fields = ('publication',)
    readonly_fields = ('image_format', 'width', 'height', 'file_size')
