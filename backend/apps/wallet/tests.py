from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from .models import Wallet, WalletTransaction
from .services import deposit_funds, withdraw_funds

User = get_user_model()


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
