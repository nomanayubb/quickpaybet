from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.wallet.models import Wallet, WalletTransaction
from apps.wallet.services import deposit_funds, withdraw_funds

from .models import CashbackConfig, CashbackCredit
from .services import credit_cashback_for_loss, get_effective_settings, record_wager

User = get_user_model()


class CashbackServiceTests(TestCase):
    def setUp(self):
        CashbackConfig.objects.filter(pk=1).delete()
        self.config = CashbackConfig.objects.create(
            pk=1, is_enabled=True, default_rate=Decimal('10.00'),
            default_wagering_multiplier=Decimal('5.00'), deduct_original_on_unlock=False,
        )
        self.user = User.objects.create_user(email='cbuser@example.com', password='testpass123')
        self.wallet = Wallet.objects.get(user=self.user)
        deposit_funds(self.user, Decimal('1000'))
        self.wallet.refresh_from_db()

    def test_no_credit_when_globally_disabled(self):
        self.config.is_enabled = False
        self.config.save(update_fields=['is_enabled'])
        credit = credit_cashback_for_loss(self.user, Decimal('100'), 'test loss')
        self.assertIsNone(credit)
        self.assertEqual(CashbackCredit.objects.count(), 0)

    def test_credit_calculation_basic(self):
        # loss=100, rate=10% -> cashback=10; multiplier=5 -> required=50
        credit = credit_cashback_for_loss(self.user, Decimal('100'), 'Bet #1 loss')
        self.assertIsNotNone(credit)
        self.assertEqual(credit.cashback_amount, Decimal('10.00000000'))
        self.assertEqual(credit.wagering_required, Decimal('50.00000000'))
        self.assertEqual(credit.status, CashbackCredit.Status.LOCKED)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1010'))
        self.assertEqual(self.wallet.locked_cashback_balance, Decimal('10'))
        self.assertEqual(self.wallet.withdrawable_balance, Decimal('1000'))  # the +10 is locked, not withdrawable

        txn = WalletTransaction.objects.get(txn_type=WalletTransaction.TxnType.CASHBACK_CREDIT)
        self.assertEqual(txn.amount, Decimal('10.00000000'))

    def test_per_user_override_enables_when_global_disabled(self):
        self.config.is_enabled = False
        self.config.save(update_fields=['is_enabled'])
        self.user.cashback_enabled_override = True
        self.user.cashback_rate_override = Decimal('20.00')
        self.user.save(update_fields=['cashback_enabled_override', 'cashback_rate_override'])

        credit = credit_cashback_for_loss(self.user, Decimal('100'), 'test loss')
        self.assertIsNotNone(credit)
        self.assertEqual(credit.cashback_amount, Decimal('20.00000000'))

    def test_per_user_override_disables_when_global_enabled(self):
        self.user.cashback_enabled_override = False
        self.user.save(update_fields=['cashback_enabled_override'])

        credit = credit_cashback_for_loss(self.user, Decimal('100'), 'test loss')
        self.assertIsNone(credit)

    def test_get_effective_settings_precedence(self):
        # No overrides -> pure global values.
        enabled, rate, multiplier, deduct = get_effective_settings(self.user)
        self.assertEqual((enabled, rate, multiplier, deduct), (True, Decimal('10.00'), Decimal('5.00'), False))

        # Override only the rate -> multiplier/enabled still inherited.
        self.user.cashback_rate_override = Decimal('7.50')
        self.user.save(update_fields=['cashback_rate_override'])
        enabled, rate, multiplier, deduct = get_effective_settings(self.user)
        self.assertEqual((enabled, rate, multiplier), (True, Decimal('7.50'), Decimal('5.00')))

    def test_wagering_progress_unlocks_at_exact_threshold_not_before(self):
        credit = credit_cashback_for_loss(self.user, Decimal('100'), 'Bet #1 loss')  # required = 50
        self.wallet.refresh_from_db()

        # Wager 49 - just short of the 50 target.
        record_wager(self.wallet, Decimal('49'))
        credit.refresh_from_db()
        self.assertEqual(credit.status, CashbackCredit.Status.LOCKED)
        self.assertEqual(credit.wagering_progress, Decimal('49'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.locked_cashback_balance, Decimal('10'))

        # One more unit of wager reaches exactly 50 -> unlocks.
        record_wager(self.wallet, Decimal('1'))
        credit.refresh_from_db()
        self.assertEqual(credit.status, CashbackCredit.Status.UNLOCKED)
        self.assertIsNotNone(credit.unlocked_at)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.locked_cashback_balance, Decimal('0'))
        # No deduction (deduct_original_on_unlock is False) - full balance retained.
        self.assertEqual(self.wallet.balance, Decimal('1010'))
        self.assertEqual(self.wallet.withdrawable_balance, Decimal('1010'))

    def test_wager_overshoot_caps_progress_at_required_not_beyond(self):
        credit = credit_cashback_for_loss(self.user, Decimal('100'), 'Bet #1 loss')  # required = 50
        self.wallet.refresh_from_db()
        record_wager(self.wallet, Decimal('500'))  # way more than the 50 needed
        credit.refresh_from_db()
        self.assertEqual(credit.wagering_progress, Decimal('50.00000000'))  # capped, not 500
        self.assertEqual(credit.status, CashbackCredit.Status.UNLOCKED)

    def test_multiple_open_credits_each_get_full_stake_independently(self):
        credit_a = credit_cashback_for_loss(self.user, Decimal('100'), 'loss A')  # required 50
        credit_b = credit_cashback_for_loss(self.user, Decimal('40'), 'loss B')   # cashback 4, required 20
        self.wallet.refresh_from_db()

        record_wager(self.wallet, Decimal('20'))

        credit_a.refresh_from_db()
        credit_b.refresh_from_db()
        # Both credits received the SAME 20 independently, not split between them.
        self.assertEqual(credit_a.wagering_progress, Decimal('20'))
        self.assertEqual(credit_a.status, CashbackCredit.Status.LOCKED)
        self.assertEqual(credit_b.wagering_progress, Decimal('20.00000000'))
        self.assertEqual(credit_b.status, CashbackCredit.Status.UNLOCKED)  # 20 >= its 20 requirement

    def test_deduct_original_on_unlock_true(self):
        self.config.deduct_original_on_unlock = True
        self.config.save(update_fields=['deduct_original_on_unlock'])

        credit = credit_cashback_for_loss(self.user, Decimal('100'), 'Bet #1 loss')  # cashback 10, required 50
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1010'))

        record_wager(self.wallet, Decimal('50'))

        credit.refresh_from_db()
        self.assertEqual(credit.status, CashbackCredit.Status.UNLOCKED)
        self.wallet.refresh_from_db()
        # The original 10 cashback is clawed back at unlock.
        self.assertEqual(self.wallet.balance, Decimal('1000'))
        self.assertEqual(self.wallet.locked_cashback_balance, Decimal('0'))

        clawback = WalletTransaction.objects.get(txn_type=WalletTransaction.TxnType.CASHBACK_CLAWBACK)
        self.assertEqual(clawback.amount, Decimal('-10'))

    def test_deduct_original_on_unlock_floors_at_zero(self):
        self.config.deduct_original_on_unlock = True
        self.config.save(update_fields=['deduct_original_on_unlock'])

        credit = credit_cashback_for_loss(self.user, Decimal('100'), 'Bet #1 loss')  # cashback 10
        self.wallet.refresh_from_db()

        # Simulate the user's balance dropping below the cashback amount
        # before unlock (e.g. they lost subsequent bets while wagering).
        self.wallet.balance = Decimal('4')
        self.wallet.save(update_fields=['balance'])

        record_wager(self.wallet, Decimal('50'))

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0'))  # floored, never negative
        clawback = WalletTransaction.objects.get(txn_type=WalletTransaction.TxnType.CASHBACK_CLAWBACK)
        self.assertEqual(clawback.amount, Decimal('-4'))  # only what was actually available

    def test_multiplier_zero_credits_immediately_unlocked(self):
        self.config.default_wagering_multiplier = Decimal('0')
        self.config.save(update_fields=['default_wagering_multiplier'])

        credit = credit_cashback_for_loss(self.user, Decimal('100'), 'Bet #1 loss')
        self.assertEqual(credit.status, CashbackCredit.Status.UNLOCKED)
        self.assertIsNotNone(credit.unlocked_at)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.locked_cashback_balance, Decimal('0'))
        self.assertEqual(self.wallet.withdrawable_balance, self.wallet.balance)

    def test_withdraw_blocked_until_cashback_unlocked(self):
        credit_cashback_for_loss(self.user, Decimal('100'), 'Bet #1 loss')  # +10 balance, 10 locked
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1010'))
        self.assertEqual(self.wallet.withdrawable_balance, Decimal('1000'))

        with self.assertRaises(Exception):
            withdraw_funds(self.user, Decimal('1005'))  # more than withdrawable

        txn = withdraw_funds(self.user, Decimal('1000'))  # exactly withdrawable
        self.assertEqual(txn.amount, Decimal('-1000'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('10'))
        self.assertEqual(self.wallet.locked_cashback_balance, Decimal('10'))
        self.assertEqual(self.wallet.withdrawable_balance, Decimal('0'))
