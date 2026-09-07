from decimal import Decimal

from django.contrib import admin, messages

from .models import (
    Sport, Tournament, Match, HouseLiquidityConfig, OddsAdjustmentConfig,
    RealtimeOddsConfig, OddsHistoryEntry, UserMatchOddsOverride,
)
from apps.bets.services import settle_bets_for_match
from apps.exchange.services import settle_exchange_for_match


@admin.register(Sport)
class SportAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = ('name', 'sport', 'is_active')
    list_filter = ('sport', 'is_active')
    search_fields = ('name',)


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ('home_team', 'away_team', 'sport', 'status', 'start_time', 'odds_adjustment_flag')
    list_filter = ('sport', 'status')
    search_fields = ('home_team', 'away_team')
    date_hierarchy = 'start_time'
    filter_horizontal = ()

    @admin.display(description='Odds override')
    def odds_adjustment_flag(self, obj):
        return 'Active' if obj.odds_adjustment is not None else ''

    fieldsets = (
        (None, {
            'fields': ('sport', 'tournament', 'home_team', 'away_team', 'start_time', 'status')
        }),
        ('Score', {
            'fields': ('home_score', 'away_score')
        }),
        ('Odds', {
            'fields': ('odds_home', 'odds_draw', 'odds_away')
        }),
        ('Odds adjustment', {
            'fields': ('odds_adjustment',),
            'description': (
                'Optional per-match override added to this match\'s odds on every refresh. '
                'Leave blank to use the site-wide default (Odds Adjustment Config, below).'
            ),
        }),
        ('House lay liquidity', {
            'fields': ('lay_spread_override', 'house_max_liability_override'),
            'description': (
                'Optional per-match overrides for the exchange\'s house-seeded lay liquidity. '
                'Leave blank to use the site-wide defaults (House Liquidity Config, below).'
            ),
        }),
    )
    actions = ['settle_selected_matches', 'cancel_selected_matches', 'reset_odds_adjustment_to_default']

    @admin.action(description='Settle selected matches')
    def settle_selected_matches(self, request, queryset):
        settled = 0
        for match in queryset:
            if match.status == Match.Status.FINISHED:
                self.message_user(request, f'{match} is already finished.', level=messages.WARNING)
                continue
            if match.status == Match.Status.CANCELLED:
                self.message_user(request, f'{match} is cancelled.', level=messages.WARNING)
                continue
            if match.home_score is None or match.away_score is None:
                self.message_user(
                    request,
                    f'Cannot settle {match}: scores are missing.',
                    level=messages.ERROR
                )
                continue

            match.status = Match.Status.FINISHED
            match.save(update_fields=['status', 'updated_at'])
            settle_bets_for_match(match)
            settle_exchange_for_match(match)
            settled += 1

        if settled:
            self.message_user(request, f'{settled} match(es) settled successfully.')

    @admin.action(description='Cancel selected matches and refund bets')
    def cancel_selected_matches(self, request, queryset):
        cancelled = 0
        for match in queryset:
            if match.status == Match.Status.CANCELLED:
                continue

            match.status = Match.Status.CANCELLED
            match.save(update_fields=['status', 'updated_at'])
            # This refunds single bets and marks all parlay legs as refunded
            settle_bets_for_match(match)
            settle_exchange_for_match(match)
            cancelled += 1

        self.message_user(request, f'{cancelled} match(es) cancelled and bets refunded.')

    @admin.action(description='Reset odds adjustment to default (site-wide) for selected matches')
    def reset_odds_adjustment_to_default(self, request, queryset):
        updated = queryset.exclude(odds_adjustment=None).update(odds_adjustment=None)
        self.message_user(request, f'Cleared the odds adjustment override on {updated} match(es).')


@admin.register(RealtimeOddsConfig)
class RealtimeOddsConfigAdmin(admin.ModelAdmin):
    list_display = (
        'is_enabled', 'live_refresh_seconds', 'not_started_refresh_seconds',
        'proximity_boost_seconds', 'proximity_window_minutes',
        'min_viewers_for_realtime', 'bet_acceptance_delay_seconds',
        'store_full_odds_history', 'updated_at',
    )

    def has_add_permission(self, request):
        # Singleton - only one row should ever exist.
        return not RealtimeOddsConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OddsAdjustmentConfig)
class OddsAdjustmentConfigAdmin(admin.ModelAdmin):
    list_display = ('default_adjustment', 'adjustment_flag', 'updated_at')
    actions = ['reset_to_default']

    @admin.display(description='Status')
    def adjustment_flag(self, obj):
        return 'Active' if obj.default_adjustment else 'Default (0)'

    @admin.action(description='Reset to no adjustment (0)')
    def reset_to_default(self, request, queryset):
        updated = queryset.update(default_adjustment=Decimal('0'))
        self.message_user(request, f'Reset {updated} config row(s) to 0.')

    def has_add_permission(self, request):
        # Singleton - only one row should ever exist.
        return not OddsAdjustmentConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HouseLiquidityConfig)
class HouseLiquidityConfigAdmin(admin.ModelAdmin):
    list_display = ('is_enabled', 'default_lay_spread', 'default_max_liability_per_selection', 'updated_at')

    def has_add_permission(self, request):
        # Singleton - only one row should ever exist.
        return not HouseLiquidityConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserMatchOddsOverride)
class UserMatchOddsOverrideAdmin(admin.ModelAdmin):
    """
    Every row here IS the alert - its mere existence means a specific
    user has a specific-match odds override active. Deleting a row (via
    Django admin's own bulk delete, no custom action needed) is exactly
    the reset-to-default action: the resolution cascade
    (apps.sports.pricing.resolve_user_extra_adjustment) automatically
    falls back to that user's global override or the site default the
    moment the row is gone.
    """
    list_display = ('user', 'match', 'adjustment', 'updated_at')
    search_fields = ('user__email', 'match__home_team', 'match__away_team')
    list_filter = ('match__sport',)
    autocomplete_fields = ('user', 'match')


@admin.register(OddsHistoryEntry)
class OddsHistoryEntryAdmin(admin.ModelAdmin):
    list_display = ('match', 'odds_home', 'odds_draw', 'odds_away', 'recorded_at')
    list_filter = ('match__sport',)
    search_fields = ('match__home_team', 'match__away_team')
    date_hierarchy = 'recorded_at'
    readonly_fields = ('match', 'odds_home', 'odds_draw', 'odds_away', 'recorded_at')

    def has_add_permission(self, request):
        # Only ever written automatically by apps.sports.realtime - never
        # manually created.
        return False
