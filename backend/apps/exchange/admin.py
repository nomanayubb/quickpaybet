from django.contrib import admin

from .models import ExchangeConfig, ExchangeFill, ExchangeOrder


@admin.register(ExchangeConfig)
class ExchangeConfigAdmin(admin.ModelAdmin):
    list_display = ('commission_rate', 'updated_at')

    def has_add_permission(self, request):
        # Singleton - only one row should ever exist.
        return not ExchangeConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExchangeOrder)
class ExchangeOrderAdmin(admin.ModelAdmin):
    list_display = ('user', 'match', 'selection', 'side', 'odds', 'stake', 'matched_stake', 'status', 'created_at')
    list_filter = ('side', 'status', 'selection')
    search_fields = ('user__email', 'match__home_team', 'match__away_team')
    date_hierarchy = 'created_at'
    readonly_fields = ('matched_stake',)


@admin.register(ExchangeFill)
class ExchangeFillAdmin(admin.ModelAdmin):
    list_display = ('match', 'selection', 'back_order', 'lay_order', 'odds', 'stake', 'status', 'commission_amount', 'created_at')
    list_filter = ('status', 'selection')
    search_fields = ('match__home_team', 'match__away_team')
    date_hierarchy = 'created_at'
