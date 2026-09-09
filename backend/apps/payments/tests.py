from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.wallet.models import FxRateConfig, Wallet

from .models import CryptoPayment
from .services import create_deposit, create_withdrawal_request, handle_deposit_success

User = get_user_model()


class PkrDepositTests(TestCase):
    """
    PAYMENT_PROVIDER defaults to 'mock' (see apps.payments.services.get_payment_provider_wrapper),
    same as apps.casino.tests's use of MockCasinoProvider - no patching needed.
    """

    def setUp(self):
        FxRateConfig.objects.update_or_create(pk=1, defaults={'usd_pkr_rate': Decimal('280.0000')})
        self.pkr_user = User.objects.create_user(email='pkrdep@example.com', password='testpass', currency='PKR')
        self.usd_user = User.objects.create_user(email='usddep@example.com', password='testpass', currency='USD')

    def test_pkr_deposit_snapshots_wallet_amount_in_pkr(self):
        # NOWPayments is still charged 10 USD (its only rail) - the wallet
        # side is snapshotted separately at 10 * 280.0000 = 2800.00 PKR.
        payment = create_deposit(user=self.pkr_user, amount=Decimal('10'), currency='USDT')
        self.assertEqual(payment.amount, Decimal('10'))
        self.assertEqual(payment.wallet_amount, Decimal('2800.00'))
        self.assertEqual(payment.wallet_currency, 'PKR')
        self.assertEqual(payment.fx_rate_applied, Decimal('280.0000'))

    def test_pkr_deposit_credits_pkr_amount_not_usd_amount_on_success(self):
        payment = create_deposit(user=self.pkr_user, amount=Decimal('10'), currency='USDT')
        handle_deposit_success(payment.external_id)

        wallet = Wallet.objects.get(user=self.pkr_user)
        self.assertEqual(wallet.balance, Decimal('2800.00'))  # not 10

    def test_usd_deposit_unaffected_rate_1_to_1(self):
        payment = create_deposit(user=self.usd_user, amount=Decimal('10'), currency='USDT')
        self.assertEqual(payment.wallet_amount, Decimal('10'))
        self.assertEqual(payment.wallet_currency, 'USD')
        self.assertEqual(payment.fx_rate_applied, Decimal('1.0000'))

        handle_deposit_success(payment.external_id)
        wallet = Wallet.objects.get(user=self.usd_user)
        self.assertEqual(wallet.balance, Decimal('10'))

    def test_later_rate_change_never_affects_an_already_created_payment(self):
        # Snapshot-on-write, same discipline as CasinoSession.fx_rate_applied -
        # a rate change after the fact must never retroactively change what
        # was already quoted/promised to the user.
        payment = create_deposit(user=self.pkr_user, amount=Decimal('10'), currency='USDT')
        FxRateConfig.objects.update_or_create(pk=1, defaults={'usd_pkr_rate': Decimal('300.0000')})

        handle_deposit_success(payment.external_id)
        wallet = Wallet.objects.get(user=self.pkr_user)
        self.assertEqual(wallet.balance, Decimal('2800.00'))  # still the rate quoted at deposit time


class PkrWithdrawalTests(TestCase):
    def setUp(self):
        FxRateConfig.objects.update_or_create(pk=1, defaults={'usd_pkr_rate': Decimal('280.0000')})
        self.pkr_user = User.objects.create_user(email='pkrwd@example.com', password='testpass', currency='PKR')
        self.wallet = Wallet.objects.get(user=self.pkr_user)
        self.wallet.balance = Decimal('5000.00')
        self.wallet.save(update_fields=['balance'])

    def test_pkr_withdrawal_debits_pkr_amount_sends_usd_to_provider(self):
        # User requests a 10 USD-equivalent withdrawal; wallet is debited
        # 10 * 280.0000 = 2800.00 PKR, but the provider payout (mock here,
        # NOWPayments in production) is still handed exactly 10.
        payment = create_withdrawal_request(
            user=self.pkr_user, amount=Decimal('10'), address='mockaddr123', currency='USDT',
        )
        self.assertEqual(payment.amount, Decimal('10'))
        self.assertEqual(payment.wallet_amount, Decimal('2800.00'))
        self.assertEqual(payment.wallet_currency, 'PKR')

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('2200.00'))  # 5000 - 2800

    def test_pkr_withdrawal_refund_on_provider_failure_refunds_pkr_amount(self):
        from unittest.mock import patch

        from rest_framework.exceptions import ValidationError

        with patch('apps.payments.services.get_payment_provider') as mock_get_provider:
            mock_provider = mock_get_provider.return_value
            mock_provider.name = 'mock'
            mock_provider.create_withdrawal.side_effect = RuntimeError('provider down')

            with self.assertRaises(ValidationError):
                create_withdrawal_request(
                    user=self.pkr_user, amount=Decimal('10'), address='mockaddr123', currency='USDT',
                )

        payment = CryptoPayment.objects.get(user=self.pkr_user)
        self.assertEqual(payment.status, CryptoPayment.Status.FAILED)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('5000.00'))  # fully refunded in PKR, not left short
