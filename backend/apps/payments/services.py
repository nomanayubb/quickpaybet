import os
import uuid
from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.wallet.services import deposit_funds

from .models import CryptoPayment
from .providers import get_payment_provider


def create_deposit(user, amount: Decimal, currency: str = 'USDT', provider: str | None = None) -> CryptoPayment:
    """
    Asks the configured payment provider for a deposit address,
    stores a PENDING CryptoPayment, and returns that record.
    """
    if amount <= 0:
        raise ValidationError('Amount must be positive.')

    if provider is None:
        provider = os.getenv('PAYMENT_PROVIDER', 'mock').lower()

    provider_obj = get_payment_provider_wrapper(provider)

    provider_result = provider_obj.create_deposit(
        user=user,
        amount=amount,
        currency=currency,
    )

    external_id = provider_result.get('external_id', '')
    address = provider_result.get('address', '')

    return CryptoPayment.objects.create(
        user=user,
        amount=amount,
        currency=currency,
        payment_type=CryptoPayment.PaymentType.DEPOSIT,
        status=CryptoPayment.Status.PENDING,
        provider=provider_result.get('provider', provider_obj.name),
        external_id=external_id,
        address=address,
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


def get_payment_provider_wrapper(provider_name: str = ''):
    """
    Convenience wrapper to keep imports clean in this file.
    """
    if not provider_name:
        provider_name = os.getenv('PAYMENT_PROVIDER', 'mock').lower()
    provider_map = {}

    # Import here to avoid circular dependency at module load time.
    from .providers import MockPaymentProvider, NOWPaymentsProvider

    provider_map['mock'] = MockPaymentProvider
    provider_map['nowpayments'] = NOWPaymentsProvider

    try:
        provider_class = provider_map[provider_name]
    except KeyError:
        raise NotImplementedError(f'Unknown payment provider: {provider_name}')

    return provider_class()
