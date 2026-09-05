from rest_framework import serializers

from apps.sports.models import Match
from .models import Bet
from .services import place_bet


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
