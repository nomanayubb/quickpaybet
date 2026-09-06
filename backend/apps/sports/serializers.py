from decimal import Decimal

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


class MatchOddsUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Match
        fields = ('id', 'odds_home', 'odds_draw', 'odds_away', 'status')
        read_only_fields = ('id',)

    def _validate_odds_value(self, value, field_name):
        if value is None:
            return value
        # Decimal odds below 1 are mathematically meaningless (a bettor must
        # always at least get their stake back on a win); reject anything
        # that would let a mistake or a compromised admin session corrupt
        # settlement math (a zero/negative odds value would produce a zero
        # or negative payout).
        if value <= Decimal('1.00'):
            raise serializers.ValidationError(
                {field_name: 'Odds must be greater than 1.00.'}
            )
        return value

    def validate_odds_home(self, value):
        return self._validate_odds_value(value, 'odds_home')

    def validate_odds_draw(self, value):
        return self._validate_odds_value(value, 'odds_draw')

    def validate_odds_away(self, value):
        return self._validate_odds_value(value, 'odds_away')
