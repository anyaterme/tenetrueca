from django.contrib import admin

from locations.models import RecyclingCenter


@admin.register(RecyclingCenter)
class RecyclingCenterAdmin(admin.ModelAdmin):
    list_display = ('name', 'operational_status', 'show_on_public_map', 'is_active', 'phone', 'email')
    list_filter = ('operational_status', 'show_on_public_map', 'is_active')
    search_fields = ('name', 'address')
    prepopulated_fields = {'slug': ('name',)}
