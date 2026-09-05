from django.urls import path

from .views import (
    WalletDetailView,
    WalletTransactionListView,
    DepositView,
    WithdrawView,
)

app_name = 'wallet'

urlpatterns = [
    path('wallet/', WalletDetailView.as_view(), name='wallet-detail'),
    path('wallet/transactions/', WalletTransactionListView.as_view(), name='wallet-transactions'),
    path('wallet/deposit/', DepositView.as_view(), name='wallet-deposit'),
    path('wallet/withdraw/', WithdrawView.as_view(), name='wallet-withdraw'),
]
