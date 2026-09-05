from rest_framework import generics, permissions
from rest_framework.response import Response

from .models import Bet, ParlayBet
from .serializers import (
    BetSerializer,
    PlaceBetSerializer,
    PlaceParlayBetSerializer,
    ParlayBetSerializer,
)


class PlaceBetView(generics.CreateAPIView):
    serializer_class = PlaceBetSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        bet = serializer.save()
        return Response(BetSerializer(bet).data, status=201)


class MyBetsListView(generics.ListAPIView):
    serializer_class = BetSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Bet.objects.filter(user=self.request.user).select_related('match', 'match__sport')


class PlaceParlayBetView(generics.CreateAPIView):
    serializer_class = PlaceParlayBetSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parlay = serializer.save()
        return Response(ParlayBetSerializer(parlay).data, status=201)


class MyParlayBetsListView(generics.ListAPIView):
    serializer_class = ParlayBetSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ParlayBet.objects.filter(user=self.request.user).prefetch_related('legs__match')
