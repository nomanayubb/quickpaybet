from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import User, PasswordResetToken


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'role', 'parent', 'commission_rate', 'is_staff', 'is_active', 'is_house_account')
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
        ('House account (danger zone)', {
            'fields': ('is_house_account', 'fund_house_account_link'),
            'description': (
                'At most one user can ever be flagged - enforced at the database level. '
                'The exchange auto-seeds synthetic lay liquidity as this user when House '
                'Liquidity Config is enabled. Fund it using the link below, never by '
                'editing its wallet directly.'
            ),
        }),
    )
    readonly_fields = ('fund_house_account_link',)

    def fund_house_account_link(self, obj):
        if obj is None or not obj.pk or not obj.is_house_account:
            return '—'
        url = reverse('admin:accounts_user_fund_house_account', args=[obj.pk])
        return format_html('<a class="button" href="{}">Deposit funds into this house account</a>', url)
    fund_house_account_link.short_description = 'Fund house account'
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

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_house_account:
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset.exclude(is_house_account=True))

    def get_urls(self):
        custom_urls = [
            path(
                '<int:user_id>/fund-house-account/',
                self.admin_site.admin_view(self.fund_house_account_view),
                name='accounts_user_fund_house_account',
            ),
        ]
        return custom_urls + super().get_urls()

    def fund_house_account_view(self, request, user_id):
        from apps.wallet.services import deposit_funds

        house_user = get_object_or_404(User, pk=user_id, is_house_account=True)
        change_url = reverse('admin:accounts_user_change', args=[house_user.id])

        if request.method == 'POST':
            amount_raw = request.POST.get('amount', '').strip()
            try:
                amount = Decimal(amount_raw)
                if amount <= 0:
                    raise InvalidOperation('must be positive')
            except InvalidOperation:
                self.message_user(request, 'Enter a valid, positive amount.', level=messages.ERROR)
                return redirect(request.path)
            deposit_funds(house_user, amount, description='House account funding (admin)')
            self.message_user(request, f'Deposited {amount} into {house_user.email}.')
            return redirect(change_url)

        return render(
            request,
            'admin/accounts/fund_house_account.html',
            {'house_user': house_user, 'change_url': change_url, 'opts': self.model._meta},
        )


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'token', 'is_used', 'created_at')
    list_filter = ('is_used', 'created_at')
    search_fields = ('user__email', 'token')
    readonly_fields = ('user', 'token', 'is_used', 'created_at')
