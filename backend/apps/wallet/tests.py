from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from .models import FxRateConfig, Wallet, WalletTransaction
from .services import convert_usd_to_wallet_currency, deposit_funds, withdraw_funds

User = get_user_model()


class ConvertUsdToWalletCurrencyTests(TestCase):
    def test_usd_user_passthrough_no_conversion(self):
        user = User.objects.create_user(email='usduser@example.com', password='testpass', currency='USD')
        wallet_amount, wallet_currency, rate = convert_usd_to_wallet_currency(user, Decimal('10'))
        self.assertEqual(wallet_amount, Decimal('10'))
        self.assertEqual(wallet_currency, 'USD')
        self.assertEqual(rate, Decimal('1.0000'))

    def test_pkr_user_converts_at_configured_rate(self):
        # 10 USD * 280.0000 rate = exactly 2800.00 PKR - hand-computed, not
        # just algebra, per this project's standing money-math test rule.
        FxRateConfig.objects.update_or_create(pk=1, defaults={'usd_pkr_rate': Decimal('280.0000')})
        user = User.objects.create_user(email='pkruser@example.com', password='testpass', currency='PKR')
        wallet_amount, wallet_currency, rate = convert_usd_to_wallet_currency(user, Decimal('10'))
        self.assertEqual(wallet_amount, Decimal('2800.00'))
        self.assertEqual(wallet_currency, 'PKR')
        self.assertEqual(rate, Decimal('280.0000'))

    def test_pkr_user_odd_amount_rounds_to_cents(self):
        # 7.336 USD * 276.5000 rate = 2028.404 PKR, quantized to 2028.40
        FxRateConfig.objects.update_or_create(pk=1, defaults={'usd_pkr_rate': Decimal('276.5000')})
        user = User.objects.create_user(email='pkruser2@example.com', password='testpass', currency='PKR')
        wallet_amount, _, _ = convert_usd_to_wallet_currency(user, Decimal('7.336'))
        self.assertEqual(wallet_amount, Decimal('2028.40'))


class WalletServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='wallet@example.com', password='testpass')
        self.wallet = Wallet.objects.get(user=self.user)

    def test_deposit_funds(self):
        txn = deposit_funds(self.user, Decimal('100'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100'))
        self.assertEqual(txn.txn_type, WalletTransaction.TxnType.DEPOSIT)
        self.assertEqual(txn.status, WalletTransaction.Status.COMPLETED)

    def test_withdraw_funds_success(self):
        deposit_funds(self.user, Decimal('100'))
        txn = withdraw_funds(self.user, Decimal('30'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('70'))
        self.assertEqual(txn.amount, Decimal('-30'))

    def test_withdraw_insufficient_balance(self):
        with self.assertRaises(ValidationError):
            withdraw_funds(self.user, Decimal('10'))

    def test_withdraw_cannot_touch_reserved_balance(self):
        """
        reserved_balance locks exposure for open exchange orders (see
        apps.exchange.services) - withdrawing must only ever draw from
        available_balance (balance - reserved_balance), never the raw
        balance, or money backing an unsettled bet could leave the wallet.
        """
        deposit_funds(self.user, Decimal('100'))
        self.wallet.reserved_balance = Decimal('40')
        self.wallet.save(update_fields=['reserved_balance'])

        with self.assertRaises(ValidationError):
            withdraw_funds(self.user, Decimal('61'))  # only 60 is available

        txn = withdraw_funds(self.user, Decimal('60'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('40'))
        self.assertEqual(self.wallet.reserved_balance, Decimal('40'))
        self.assertEqual(self.wallet.available_balance, Decimal('0'))
        self.assertEqual(txn.amount, Decimal('-60'))
