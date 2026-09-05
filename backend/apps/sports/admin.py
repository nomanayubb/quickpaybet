from django.contrib import admin

from .models import Sport, Tournament, Match


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
