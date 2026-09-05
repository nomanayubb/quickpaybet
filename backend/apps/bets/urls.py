from django.urls import path

from .views import PlaceBetView, MyBetsListView

app_name = 'bets'

urlpatterns = [
    path('bets/place/', PlaceBetView.as_view(), name='place-bet'),
    path('bets/', MyBetsListView.as_view(), name='my-bets'),
]
