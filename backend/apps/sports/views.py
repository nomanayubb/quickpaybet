from rest_framework import generics, permissions

from .models import Sport, Match
from .serializers import SportSerializer, MatchSerializer


class SportListView(generics.ListAPIView):
    serializer_class = SportSerializer
    permission_classes = [permissions.AllowAny]
    queryset = Sport.objects.filter(is_active=True)


class MatchListView(generics.ListAPIView):
    serializer_class = MatchSerializer
    permission_classes = [permissions.AllowAny]
    queryset = Match.objects.select_related('sport', 'tournament')


class MatchDetailView(generics.RetrieveAPIView):
    serializer_class = MatchSerializer
    permission_classes = [permissions.AllowAny]
    queryset = Match.objects.select_related('sport', 'tournament')
