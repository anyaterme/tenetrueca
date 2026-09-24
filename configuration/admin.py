from django.contrib import admin

from configuration.models import FunctionalRule


@admin.register(FunctionalRule)
class FunctionalRuleAdmin(admin.ModelAdmin):
    list_display = ('key', 'version', 'value_type', 'is_active', 'effective_from', 'effective_to')
    list_filter = ('value_type', 'is_active')
    search_fields = ('key', 'name', 'description')
