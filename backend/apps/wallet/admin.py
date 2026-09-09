from django.contrib import admin

from .models import FxRateConfig, Wallet, WalletTransaction


@admin.register(FxRateConfig)
class FxRateConfigAdmin(admin.ModelAdmin):
    list_display = ('usd_pkr_rate', 'is_manual_override', 'last_synced_at', 'last_sync_error', 'updated_at')
    readonly_fields = ('last_synced_at', 'last_sync_error', 'updated_at')

    def has_add_permission(self, request):
        return not FxRateConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'balance', 'reserved_balance', 'updated_at')
    search_fields = ('user__email',)
    readonly_fields = ('created_at', 'updated_at')

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ('wallet', 'txn_type', 'amount', 'status', 'created_at')
    list_filter = ('txn_type', 'status')
    search_fields = ('wallet__user__email', 'reference_id')
    readonly_fields = ('created_at', 'updated_at')
