from django.urls import path

from .views import SportListView, MatchListView, MatchDetailView

app_name = 'sports'

urlpatterns = [
    path('sports/', SportListView.as_view(), name='sport-list'),
    path('matches/', MatchListView.as_view(), name='match-list'),
    path('matches/<int:pk>/', MatchDetailView.as_view(), name='match-detail'),
]
