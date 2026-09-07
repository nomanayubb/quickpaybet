from django.contrib import admin, messages

from .models import Sport, Tournament, Match, RealtimeOddsConfig
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
    list_display = ('home_team', 'away_team', 'sport', 'status', 'start_time')
    list_filter = ('sport', 'status')
    search_fields = ('home_team', 'away_team')
    date_hierarchy = 'start_time'
    filter_horizontal = ()
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
    )
    actions = ['settle_selected_matches', 'cancel_selected_matches']

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


@admin.register(RealtimeOddsConfig)
class RealtimeOddsConfigAdmin(admin.ModelAdmin):
    list_display = (
        'is_enabled', 'live_refresh_seconds', 'not_started_refresh_seconds',
        'proximity_boost_seconds', 'proximity_window_minutes',
        'min_viewers_for_realtime', 'bet_acceptance_delay_seconds', 'updated_at',
    )

    def has_add_permission(self, request):
        # Singleton - only one row should ever exist.
        return not RealtimeOddsConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
