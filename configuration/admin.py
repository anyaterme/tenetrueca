from django.contrib import admin

from configuration.models import EmailConfiguration, FunctionalRule


@admin.register(FunctionalRule)
class FunctionalRuleAdmin(admin.ModelAdmin):
    list_display = ('key', 'version', 'value_type', 'is_active', 'effective_from', 'effective_to')
    list_filter = ('value_type', 'is_active')
    search_fields = ('key', 'name', 'description')


@admin.register(EmailConfiguration)
class EmailConfigurationAdmin(admin.ModelAdmin):
    fields = (
        'email_host',
        'email_port',
        'email_host_user',
        'security',
        'default_from_email',
        'email_timeout',
        'updated_by',
        'created_at',
        'updated_at',
    )
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
