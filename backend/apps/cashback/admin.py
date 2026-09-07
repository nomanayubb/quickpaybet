from django.contrib import admin

from .models import CashbackConfig, CashbackCredit


@admin.register(CashbackConfig)
class CashbackConfigAdmin(admin.ModelAdmin):
    list_display = ('is_enabled', 'default_rate', 'default_wagering_multiplier', 'deduct_original_on_unlock', 'updated_at')

    def has_add_permission(self, request):
        # Singleton - only one row should ever exist.
        return not CashbackConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CashbackCredit)
class CashbackCreditAdmin(admin.ModelAdmin):
    list_display = ('user', 'source_description', 'cashback_amount', 'wagering_required', 'wagering_progress', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('user__email', 'source_description')
    date_hierarchy = 'created_at'
    readonly_fields = (
        'user', 'source_description', 'loss_amount', 'rate_applied', 'cashback_amount',
        'multiplier_applied', 'wagering_required', 'deduct_original_on_unlock', 'created_at',
    )
