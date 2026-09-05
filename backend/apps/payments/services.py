import uuid
from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.wallet.services import deposit_funds

from .models import CryptoPayment


def create_deposit(user, amount: Decimal, currency: str = 'USDT', provider: str = 'mock') -> CryptoPayment:
    if amount <= 0:
        raise ValidationError('Amount must be positive.')

    external_id = f"dep_{uuid.uuid4().hex}"
    address = f"mockaddr_{external_id[-12:]}"  # mock address; real provider returns a real one

    return CryptoPayment.objects.create(
        user=user,
        amount=amount,
        currency=currency,
        payment_type=CryptoPayment.PaymentType.DEPOSIT,
        status=CryptoPayment.Status.PENDING,
        provider=provider,
        external_id=external_id,
        address=address
    )


def handle_deposit_success(external_id: str) -> CryptoPayment:
    """
    Called by the payment webhook when a deposit is confirmed.
    Credits the user's wallet and marks the payment completed.
    """
    try:
        payment = CryptoPayment.objects.get(
            external_id=external_id,
            payment_type=CryptoPayment.PaymentType.DEPOSIT,
            status=CryptoPayment.Status.PENDING,
        )
    except CryptoPayment.DoesNotExist:
        raise ValidationError('Pending payment not found.')

    with transaction.atomic():
        deposit_funds(
            user=payment.user,
            amount=payment.amount,
            description=f"Crypto deposit confirmed ({payment.external_id})"
        )
        payment.status = CryptoPayment.Status.COMPLETED
        payment.save(update_fields=['status', 'updated_at'])

    return payment
