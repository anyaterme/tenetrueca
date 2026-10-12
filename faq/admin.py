from django.contrib import admin
from django.db import transaction

from faq.models import FAQ
from faq.audit import faq_snapshot, record_faq_event


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ('question', 'position', 'is_published', 'updated_at')
    list_editable = ('position', 'is_published')
    search_fields = ('question', 'answer')
    list_filter = ('is_published',)
    readonly_fields = ('created_at', 'updated_at')

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        before = faq_snapshot(FAQ.objects.select_for_update().get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_faq_event(
            actor=request.user, faq_id=obj.pk, action='faq.updated' if change else 'faq.created',
            before=before, after=faq_snapshot(obj), source='django_admin',
        )

    @transaction.atomic
    def delete_model(self, request, obj):
        faq_id, before = obj.pk, faq_snapshot(FAQ.objects.select_for_update().get(pk=obj.pk))
        super().delete_model(request, obj)
        record_faq_event(actor=request.user, faq_id=faq_id, action='faq.deleted', before=before, source='django_admin')

    @transaction.atomic
    def delete_queryset(self, request, queryset):
        snapshots = [(faq.pk, faq_snapshot(faq)) for faq in queryset.select_for_update()]
        super().delete_queryset(request, queryset)
        for faq_id, before in snapshots:
            record_faq_event(actor=request.user, faq_id=faq_id, action='faq.deleted', before=before, source='django_admin')
