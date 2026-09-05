from django.urls import path

from .views import (
    PlaceBetView,
    MyBetsListView,
    PlaceParlayBetView,
    MyParlayBetsListView,
)

app_name = 'bets'

urlpatterns = [
    path('bets/place/', PlaceBetView.as_view(), name='place-bet'),
    path('bets/', MyBetsListView.as_view(), name='my-bets'),
    path('parlays/place/', PlaceParlayBetView.as_view(), name='place-parlay'),
    path('parlays/', MyParlayBetsListView.as_view(), name='my-parlays'),
]
