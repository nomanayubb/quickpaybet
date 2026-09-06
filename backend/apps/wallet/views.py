from rest_framework import generics, permissions, status
from rest_framework.response import Response

from apps.audit.services import create_audit_log
from apps.payments.serializers import CryptoPaymentSerializer
from apps.payments.services import create_deposit, create_withdrawal_request

from .models import Wallet, WalletTransaction
from .serializers import (
    WalletSerializer,
    WalletTransactionSerializer,
    DepositSerializer,
    WithdrawSerializer,
)


class WalletDetailView(generics.RetrieveAPIView):
    serializer_class = WalletSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        wallet, _ = Wallet.objects.get_or_create(user=self.request.user)
        return wallet


class WalletTransactionListView(generics.ListAPIView):
    serializer_class = WalletTransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        wallet, _ = Wallet.objects.get_or_create(user=self.request.user)
        return WalletTransaction.objects.filter(wallet=wallet)


class DepositView(generics.CreateAPIView):
    serializer_class = DepositSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payment = create_deposit(
            user=request.user,
            amount=serializer.validated_data['amount'],
            currency=serializer.validated_data.get('currency', 'USDT'),
        )
        return Response(
            CryptoPaymentSerializer(payment).data,
            status=status.HTTP_201_CREATED,
        )


class WithdrawView(generics.CreateAPIView):
    serializer_class = WithdrawSerializer
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'withdraw'

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payment = create_withdrawal_request(
            user=request.user,
            amount=serializer.validated_data['amount'],
            address=serializer.validated_data['address'],
            currency=serializer.validated_data.get('currency', 'USDT'),
        )
        create_audit_log(
            user=request.user,
            action='withdrawal_requested',
            target_type='crypto_payment',
            target_id=payment.id,
            metadata={'amount': str(payment.amount), 'address': payment.address},
            ip_address=request.META.get('REMOTE_ADDR'),
        )
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        return Response(
            {
                'payment': CryptoPaymentSerializer(payment).data,
                'wallet': WalletSerializer(wallet).data,
            },
            status=status.HTTP_201_CREATED,
        )
