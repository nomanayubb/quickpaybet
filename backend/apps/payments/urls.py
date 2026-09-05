from django.urls import path

from .views import PaymentWebhookView

app_name = 'payments'

urlpatterns = [
    path('webhook/', PaymentWebhookView.as_view(), name='payment-webhook'),
]
