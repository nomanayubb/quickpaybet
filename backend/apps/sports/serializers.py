from rest_framework import serializers

from .models import Sport, Tournament, Match


class SportSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sport
        fields = ('id', 'name', 'slug', 'is_active')
        read_only_fields = ('id',)


class TournamentSerializer(serializers.ModelSerializer):
    sport_name = serializers.CharField(source='sport.name', read_only=True)

    class Meta:
        model = Tournament
        fields = (
            'id', 'sport', 'sport_name', 'name', 'season', 'is_active'
        )
        read_only_fields = ('id',)


class MatchSerializer(serializers.ModelSerializer):
    sport_name = serializers.CharField(source='sport.name', read_only=True)
    tournament_name = serializers.CharField(
        source='tournament.name',
        read_only=True,
        default=None
    )

    class Meta:
        model = Match
        fields = (
            'id',
            'sport',
            'sport_name',
            'tournament',
            'tournament_name',
            'home_team',
            'away_team',
            'start_time',
            'status',
            'home_score',
            'away_score',
            'odds_home',
            'odds_draw',
            'odds_away',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')
