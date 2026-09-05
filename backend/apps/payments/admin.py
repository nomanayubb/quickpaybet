from django.contrib import admin

from .models import CryptoPayment


@admin.register(CryptoPayment)
class CryptoPaymentAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'external_id', 'payment_type', 'amount', 'currency',
        'status', 'provider', 'created_at'
    )
    list_filter = ('payment_type', 'status', 'currency')
    search_fields = ('user__email', 'external_id')
