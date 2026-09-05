from django.urls import path

from .views import WalletDetailView, WalletTransactionListView

app_name = 'wallet'

urlpatterns = [
    path('wallet/', WalletDetailView.as_view(), name='wallet-detail'),
    path('wallet/transactions/', WalletTransactionListView.as_view(), name='wallet-transactions'),
]
