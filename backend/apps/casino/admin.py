from django.contrib import admin

from .models import CasinoBrand, CasinoConfig, CasinoGame, CasinoRoundSettlement, CasinoSession, CasinoWalletEvent


@admin.register(CasinoConfig)
class CasinoConfigAdmin(admin.ModelAdmin):
    list_display = ('is_enabled', 'max_launch_balance', 'provider_currency_code', 'usd_to_provider_currency_rate', 'updated_at')

    def has_add_permission(self, request):
        # Singleton - only one row should ever exist.
        return not CasinoConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CasinoBrand)
class CasinoBrandAdmin(admin.ModelAdmin):
    list_display = ('name', 'brand_id', 'game_count', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    actions = ['enable_selected', 'disable_selected']

    @admin.action(description='Enable selected brands')
    def enable_selected(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'Enabled {updated} brand(s).')

    @admin.action(description='Disable selected brands')
    def disable_selected(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'Disabled {updated} brand(s).')


@admin.register(CasinoGame)
class CasinoGameAdmin(admin.ModelAdmin):
    list_display = ('name', 'brand', 'category', 'game_uid', 'is_active', 'updated_at')
    list_filter = ('is_active', 'brand', 'category')
    search_fields = ('name', 'game_uid')
    actions = ['enable_selected', 'disable_selected']

    @admin.action(description='Enable selected games')
    def enable_selected(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'Enabled {updated} game(s).')

    @admin.action(description='Disable selected games')
    def disable_selected(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'Disabled {updated} game(s).')


@admin.register(CasinoSession)
class CasinoSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'game', 'opened_balance_usd', 'provider_currency_code', 'status', 'launched_at')
    list_filter = ('status',)
    search_fields = ('user__email',)
    readonly_fields = [f.name for f in CasinoSession._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(CasinoRoundSettlement)
class CasinoRoundSettlementAdmin(admin.ModelAdmin):
    list_display = ('session', 'provider_serial_number', 'bet_amount', 'win_amount', 'created_at')
    search_fields = ('provider_serial_number',)
    readonly_fields = [f.name for f in CasinoRoundSettlement._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(CasinoWalletEvent)
class CasinoWalletEventAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'amount', 'round_id', 'is_rollback', 'cash_skipped', 'created_at')
    list_filter = ('action', 'is_rollback', 'cash_skipped')
    search_fields = ('call_id', 'round_id', 'user__email')
    readonly_fields = [f.name for f in CasinoWalletEvent._meta.fields]

    def has_add_permission(self, request):
        return False
