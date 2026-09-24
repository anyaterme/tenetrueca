from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from accounts.models import User, UserCenterAccess


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ('email',)
    list_display = ('email', 'first_name', 'last_name', 'account_status', 'is_staff')
    list_filter = ('account_status', 'is_staff', 'is_superuser', 'is_active')
    search_fields = ('email', 'first_name', 'last_name', 'nif_nie')
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Datos personales', {'fields': ('first_name', 'last_name', 'nif_nie', 'phone')}),
        ('TRUEC@', {'fields': ('habitual_recycling_center', 'account_status', 'preferences')}),
        ('SSO y conciliacion', {'fields': ('external_identity_id', 'reconciled_from_user')}),
        ('Consentimientos', {'fields': ('consent_version', 'consent_accepted_at')}),
        ('Permisos', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Fechas', {'fields': ('last_login', 'date_joined', 'deactivated_at')}),
    )
    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': ('email', 'first_name', 'password1', 'password2'),
            },
        ),
    )


@admin.register(UserCenterAccess)
class UserCenterAccessAdmin(admin.ModelAdmin):
    list_display = ('user', 'center', 'role', 'is_active')
    list_filter = ('role', 'is_active', 'center')
    search_fields = ('user__email', 'center__name')
