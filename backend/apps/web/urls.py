from django.urls import path
from django.contrib.auth import views as auth_views

from .views import (
    home_view,
    register_view,
    login_view,
    logout_view,
    matches_view,
    match_detail_view,
    wallet_view,
    wallet_deposit_view,
    wallet_withdraw_view,
    bet_history_view,
)

app_name = 'web'

urlpatterns = [
    path('', home_view, name='home'),
    path('register/', register_view, name='register'),
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('matches/', matches_view, name='matches'),
    path('matches/<int:pk>/', match_detail_view, name='match_detail'),
    path('wallet/', wallet_view, name='wallet'),
    path('wallet/deposit/', wallet_deposit_view, name='wallet_deposit'),
    path('wallet/withdraw/', wallet_withdraw_view, name='wallet_withdraw'),
    path('bets/', bet_history_view, name='bets'),
]
