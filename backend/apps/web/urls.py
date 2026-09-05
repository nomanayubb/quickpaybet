from django.urls import path
from django.contrib.auth import views as auth_views

from .views import (
    admin_dashboard_view,
    admin_matches_view,
    admin_edit_match_view,
    admin_settle_match_view,
    admin_cancel_match_view,
    admin_audit_logs_view,
    admin_users_view,
    admin_user_update_view,
    home_view,
    register_view,
    login_view,
    logout_view,
    matches_view,
    match_detail_view,
    parlay_bet_view,
    parlay_history_view,
    wallet_view,
    wallet_deposit_view,
    wallet_withdraw_view,
    confirm_deposit_view,
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
    path('admin/matches/<int:match_id>/edit/', admin_edit_match_view, name='admin_edit_match'),
    path('admin/matches/<int:match_id>/settle/', admin_settle_match_view, name='admin_settle_match'),
    path('admin/matches/<int:match_id>/cancel/', admin_cancel_match_view, name='admin_cancel_match'),
    path('admin/audit/', admin_audit_logs_view, name='admin_audit'),
    path('admin/users/', admin_users_view, name='admin_users'),
    path('admin/users/<int:user_id>/edit/', admin_user_update_view, name='admin_user_edit'),
    path('parlay/', parlay_bet_view, name='parlay_bet'),
    path('parlays/', parlay_history_view, name='parlay_history'),
    path('wallet/', wallet_view, name='wallet'),
    path('wallet/deposit/', wallet_deposit_view, name='wallet_deposit'),
    path('wallet/withdraw/', wallet_withdraw_view, name='wallet_withdraw'),
    path('wallet/confirm-deposit/<int:deposit_id>/', confirm_deposit_view, name='confirm_deposit'),
    path('bets/', bet_history_view, name='bets'),
]
