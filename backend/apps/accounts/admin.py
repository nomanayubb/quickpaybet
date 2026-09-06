from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User, PasswordResetToken


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'role', 'parent', 'commission_rate', 'is_staff', 'is_active')
    list_filter = ('role', 'is_staff', 'is_active')
    ordering = ('email',)
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'phone_number')}),
        ('Hierarchy & Limits', {
            'fields': (
                'parent',
                'role',
                'commission_rate',
                'min_bet_amount',
                'max_bet_amount',
                'is_betting_enabled',
            )
        }),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')
        }),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2'),
        }),
        ('Hierarchy & Limits', {
            'fields': (
                'parent',
                'role',
                'commission_rate',
                'min_bet_amount',
                'max_bet_amount',
                'is_betting_enabled',
            )
        }),
    )


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'token', 'is_used', 'created_at')
    list_filter = ('is_used', 'created_at')
    search_fields = ('user__email', 'token')
    readonly_fields = ('user', 'token', 'is_used', 'created_at')
