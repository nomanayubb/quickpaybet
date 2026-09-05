from django.contrib import admin

from .models import Bet


@admin.register(Bet)
class BetAdmin(admin.ModelAdmin):
    list_display = ('user', 'match', 'selection', 'odds', 'stake', 'status', 'created_at')
    list_filter = ('status', 'selection')
    search_fields = ('user__email', 'match__home_team', 'match__away_team')
    date_hierarchy = 'created_at'
    readonly_fields = ('potential_payout',)
