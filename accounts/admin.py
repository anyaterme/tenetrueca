from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from accounts.models import MagicLoginToken, User, UserCenterAccess


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ('email',)
    list_display = ('email', 'username', 'first_name', 'last_name', 'account_status', 'is_staff')
    list_filter = ('account_status', 'is_staff', 'is_superuser', 'is_active')
    search_fields = ('email', 'username', 'first_name', 'last_name', 'nif_nie')
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Datos personales', {'fields': ('username', 'first_name', 'last_name', 'nif_nie', 'phone')}),
        ('TRUEC@', {'fields': ('habitual_recycling_center', 'account_status', 'preferences')}),
        ('Autenticación y conciliación', {'fields': ('auth_source', 'external_auth_id', 'reconciled_from_user')}),
        ('Consentimientos', {'fields': ('consent_version', 'consent_accepted_at')}),
        ('Permisos', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Fechas', {'fields': ('last_login', 'date_joined', 'deactivated_at')}),
    )
    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': ('email', 'username', 'first_name', 'password1', 'password2'),
            },
        ),
    )


@admin.register(UserCenterAccess)
class UserCenterAccessAdmin(admin.ModelAdmin):
    list_display = ('user', 'center', 'role', 'is_active')
    list_filter = ('role', 'is_active', 'center')
    search_fields = ('user__email', 'center__name')


@admin.register(MagicLoginToken)
class MagicLoginTokenAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'created_at', 'expires_at', 'used_at', 'invalidated_at')
    list_filter = ('created_at', 'expires_at', 'used_at', 'invalidated_at')
    search_fields = ('user__email', 'user__username')
    readonly_fields = (
        'user',
        'token_hash',
        'created_at',
        'expires_at',
        'used_at',
        'invalidated_at',
        'requested_ip',
        'redirect_path',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
