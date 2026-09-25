from django.contrib import admin

from points.models import PointMovement


@admin.register(PointMovement)
class PointMovementAdmin(admin.ModelAdmin):
    list_display = ('user', 'amount', 'reason', 'reference', 'created_at')
    list_filter = ('reason',)
    search_fields = ('user__email', 'reference')
    readonly_fields = ('user', 'amount', 'reason', 'reference', 'operation', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
