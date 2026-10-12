from django.contrib import admin

from faq.models import FAQ


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ('question', 'position', 'is_published', 'updated_at')
    list_editable = ('position', 'is_published')
    search_fields = ('question', 'answer')
    list_filter = ('is_published',)
    readonly_fields = ('created_at', 'updated_at')
