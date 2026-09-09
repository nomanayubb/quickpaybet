from django.urls import path

from .views import CasinoCallbackView, WaijaWalletCallbackView

app_name = 'casino'

urlpatterns = [
    path('callback/', CasinoCallbackView.as_view(), name='casino-callback'),
    path('waija/callback/', WaijaWalletCallbackView.as_view(), name='waija-callback'),
]
