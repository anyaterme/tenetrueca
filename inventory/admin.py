from django.contrib import admin

from inventory.models import ReceptionInspection


@admin.register(ReceptionInspection)
class ReceptionInspectionAdmin(admin.ModelAdmin):
    list_display = ('publication', 'decision', 'center', 'operator', 'inspected_at')
    list_filter = ('decision', 'center', 'condition', 'inspected_at')
    search_fields = ('publication__title', 'publication__submitter__email', 'notes')
    readonly_fields = (
        'publication',
        'center',
        'operator',
        'decision',
        'condition',
        'internal_location',
        'notes',
        'inventory_item',
        'inspected_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
