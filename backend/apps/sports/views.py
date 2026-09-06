from rest_framework import generics, permissions

from apps.audit.services import create_audit_log
from .models import Sport, Match
from .serializers import SportSerializer, MatchSerializer, MatchOddsUpdateSerializer
from .permissions import IsAdminOrMaster


def _get_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


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


class MatchOddsUpdateView(generics.UpdateAPIView):
    serializer_class = MatchOddsUpdateSerializer
    queryset = Match.objects.all()
    permission_classes = [permissions.IsAuthenticated, IsAdminOrMaster]

    def perform_update(self, serializer):
        match = serializer.save()
        create_audit_log(
            user=self.request.user,
            action='odds_overridden',
            target_type='match',
            target_id=match.id,
            metadata={
                'odds_home': str(match.odds_home),
                'odds_draw': str(match.odds_draw),
                'odds_away': str(match.odds_away),
            },
            ip_address=_get_ip(self.request),
        )
