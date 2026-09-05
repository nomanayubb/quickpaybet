from rest_framework import serializers

from .models import CryptoPayment


class CryptoPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CryptoPayment
        fields = (
            'id', 'amount', 'currency', 'payment_type', 'status',
            'provider', 'external_id', 'address', 'created_at', 'updated_at'
        )
        read_only_fields = (
            'id', 'external_id', 'status', 'created_at', 'updated_at'
        )
