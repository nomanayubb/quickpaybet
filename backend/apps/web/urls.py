from django.urls import path
from django.contrib.auth import views as auth_views

from .views import (
    admin_dashboard_view,
    admin_matches_view,
    admin_settle_match_view,
    admin_cancel_match_view,
    admin_users_view,
    admin_user_update_view,
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
    path('dashboard/', admin_dashboard_view, name='dashboard'),
    path('admin/matches/', admin_matches_view, name='admin_matches'),
    path('admin/matches/<int:match_id>/settle/', admin_settle_match_view, name='admin_settle_match'),
    path('admin/matches/<int:match_id>/cancel/', admin_cancel_match_view, name='admin_cancel_match'),
    path('admin/users/', admin_users_view, name='admin_users'),
    path('admin/users/<int:user_id>/edit/', admin_user_update_view, name='admin_user_edit'),
    path('wallet/', wallet_view, name='wallet'),
    path('wallet/deposit/', wallet_deposit_view, name='wallet_deposit'),
    path('wallet/withdraw/', wallet_withdraw_view, name='wallet_withdraw'),
    path('bets/', bet_history_view, name='bets'),
]
