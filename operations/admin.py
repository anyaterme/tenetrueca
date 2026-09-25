from django.contrib import admin

from operations.models import Operation


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ('reference', 'operation_type', 'status', 'reusable_object', 'center', 'occurred_at')
    list_filter = ('operation_type', 'status', 'center')
    search_fields = ('reference', 'reusable_object__reference', 'reusable_object__title', 'actor__email')
    autocomplete_fields = ('actor', 'reusable_object', 'center')
    date_hierarchy = 'occurred_at'
