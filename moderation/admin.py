from django.contrib import admin

from moderation.models import ModerationDecision


@admin.register(ModerationDecision)
class ModerationDecisionAdmin(admin.ModelAdmin):
    list_display = ('publication', 'decision', 'reviewer', 'decided_at')
    list_filter = ('decision', 'decided_at')
    search_fields = ('publication__title', 'publication__submitter__email', 'notes')
    readonly_fields = (
        'publication',
        'reviewer',
        'decision',
        'previous_status',
        'resulting_status',
        'reason_code',
        'notes',
        'decided_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
