from django.contrib import admin

from audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'entity', 'entity_id', 'result')
    list_filter = ('action', 'entity', 'result', 'created_at')
    search_fields = ('entity', 'entity_id', 'actor__email')
    readonly_fields = ('actor', 'action', 'entity', 'entity_id', 'source', 'before', 'after', 'result', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
