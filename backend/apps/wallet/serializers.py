from rest_framework import serializers

from .models import Wallet, WalletTransaction


class WalletSerializer(serializers.ModelSerializer):
    available_balance = serializers.DecimalField(
        max_digits=20,
        decimal_places=8,
        read_only=True
    )

    class Meta:
        model = Wallet
        fields = (
            'id', 'balance', 'reserved_balance', 'available_balance',
            'created_at', 'updated_at'
        )
        read_only_fields = (
            'id', 'balance', 'reserved_balance', 'available_balance',
            'created_at', 'updated_at'
        )


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = (
            'id', 'wallet', 'txn_type', 'amount', 'status',
            'balance_after', 'reference_id', 'description',
            'created_at', 'updated_at'
        )
        read_only_fields = (
            'id', 'wallet', 'balance_after', 'created_at', 'updated_at'
        )
