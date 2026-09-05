from rest_framework import serializers

from apps.sports.models import Match
from .models import Bet, ParlayBet
from .services import place_bet, place_parlay_bet


class BetSerializer(serializers.ModelSerializer):
    match_label = serializers.SerializerMethodField()

    class Meta:
        model = Bet
        fields = (
            'id',
            'match',
            'match_label',
            'selection',
            'odds',
            'stake',
            'potential_payout',
            'status',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'potential_payout',
            'status',
            'created_at',
            'updated_at',
        )

    def get_match_label(self, obj):
        return f'{obj.match.home_team} v {obj.match.away_team}'


class PlaceBetSerializer(serializers.Serializer):
    match = serializers.PrimaryKeyRelatedField(queryset=Match.objects.all())
    selection = serializers.ChoiceField(choices=Bet.Selection.choices)
    stake = serializers.DecimalField(max_digits=20, decimal_places=8)

    def validate(self, attrs):
        stake = attrs['stake']
        if stake <= 0:
            raise serializers.ValidationError('Stake must be positive.')
        return attrs

    def create(self, validated_data):
        user = self.context['request'].user
        return place_bet(
            user=user,
            match_id=validated_data['match'].id,
            selection=validated_data['selection'],
            stake=validated_data['stake'],
        )


class ParlayLegSerializer(serializers.Serializer):
    match = serializers.PrimaryKeyRelatedField(queryset=Match.objects.all())
    selection = serializers.ChoiceField(choices=Bet.Selection.choices)


class PlaceParlayBetSerializer(serializers.Serializer):
    stake = serializers.DecimalField(max_digits=20, decimal_places=8)
    selections = ParlayLegSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        stake = attrs['stake']
        if stake <= 0:
            raise serializers.ValidationError('Stake must be positive.')
        if len(attrs['selections']) < 2:
            raise serializers.ValidationError(
                'A parlay bet requires at least two selections.'
            )
        return attrs

    def create(self, validated_data):
        user = self.context['request'].user
        selections = [
            {'match': item['match'].id, 'selection': item['selection']}
            for item in validated_data['selections']
        ]
        return place_parlay_bet(
            user=user,
            stake=validated_data['stake'],
            selections=selections,
        )


class ParlayBetSerializer(serializers.ModelSerializer):
    legs_detail = serializers.SerializerMethodField()

    class Meta:
        model = ParlayBet
        fields = (
            'id', 'stake', 'total_odds', 'potential_payout', 'status',
            'legs_detail', 'created_at', 'updated_at'
        )
        read_only_fields = (
            'id', 'total_odds', 'potential_payout', 'status',
            'created_at', 'updated_at'
        )

    def get_legs_detail(self, obj):
        from .models import ParlayLeg
        return [
            {
                'match': leg.match.id,
                'match_label': f'{leg.match.home_team} v {leg.match.away_team}',
                'selection': leg.selection,
                'odds': str(leg.odds),
                'outcome': leg.outcome,
            }
            for leg in obj.legs.select_related('match').all()
        ]
