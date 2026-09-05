from django.contrib import admin

from .models import Bet, ParlayBet, ParlayLeg


class ParlayLegInline(admin.TabularInline):
    model = ParlayLeg
    extra = 0
    readonly_fields = ('match', 'selection', 'odds', 'outcome')


@admin.register(Bet)
class BetAdmin(admin.ModelAdmin):
    list_display = ('user', 'match', 'selection', 'odds', 'stake', 'status', 'created_at')
    list_filter = ('status', 'selection')
    search_fields = ('user__email', 'match__home_team', 'match__away_team')
    date_hierarchy = 'created_at'
    readonly_fields = ('potential_payout',)


@admin.register(ParlayBet)
class ParlayBetAdmin(admin.ModelAdmin):
    list_display = ('user', 'stake', 'total_odds', 'potential_payout', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('user__email',)
    inlines = [ParlayLegInline]
    readonly_fields = ('potential_payout', 'created_at', 'updated_at')


@admin.register(ParlayLeg)
class ParlayLegAdmin(admin.ModelAdmin):
    list_display = ('parlay', 'match', 'selection', 'odds', 'outcome')
    list_filter = ('outcome',)
    search_fields = ('parlay__user__email', 'match__home_team', 'match__away_team')
