from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.sports.models import Sport, Match
from apps.wallet.models import Wallet
from apps.wallet.services import deposit_funds

from .models import ExchangeConfig, ExchangeFill, ExchangeOrder
from .services import cancel_order, cash_out_order, place_order, settle_exchange_for_match

User = get_user_model()


class ExchangeMatchingTests(TestCase):
    def setUp(self):
        self.sport = Sport.objects.create(name='ExchangeTestSport', slug='exchange-test-sport')
        self.match = Match.objects.create(
            sport=self.sport, home_team='H', away_team='A',
            start_time=timezone.now() + timezone.timedelta(days=1),
        )
        self.backer = User.objects.create_user(email='backer@example.com', password='testpass123')
        self.layer = User.objects.create_user(email='layer@example.com', password='testpass123')
        for u in (self.backer, self.layer):
            Wallet.objects.get_or_create(user=u)
            deposit_funds(u, Decimal('1000'))

    def _balance(self, user):
        return Wallet.objects.get(user=user).balance

    def _reserved(self, user):
        return Wallet.objects.get(user=user).reserved_balance

    def test_exact_price_match(self):
        back = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        lay = place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))

        back.refresh_from_db()
        lay.refresh_from_db()
        self.assertEqual(back.matched_stake, Decimal('100'))
        self.assertEqual(lay.matched_stake, Decimal('100'))
        self.assertEqual(ExchangeFill.objects.count(), 1)
        fill = ExchangeFill.objects.first()
        self.assertEqual(fill.odds, Decimal('2.00'))
        self.assertEqual(fill.stake, Decimal('100'))

        # Backer's exposure = stake (100), layer's exposure = stake*(odds-1) = 100
        self.assertEqual(self._reserved(self.backer), Decimal('100'))
        self.assertEqual(self._reserved(self.layer), Decimal('100'))

    def test_price_improvement_backer_gets_better_price(self):
        # Resting lay at 2.50 (better for a backer than the minimum 2.00 they ask for)
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.50'), Decimal('50'))
        back = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('50'))
        fill = ExchangeFill.objects.get()
        # Execution must be at the RESTING (lay) order's price, not the backer's ask.
        self.assertEqual(fill.odds, Decimal('2.50'))

    def test_partial_match_across_two_counter_orders(self):
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('30'))
        layer2 = User.objects.create_user(email='layer2@example.com', password='testpass123')
        Wallet.objects.get_or_create(user=layer2)
        deposit_funds(layer2, Decimal('1000'))
        place_order(layer2, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('70'))

        back = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        back.refresh_from_db()
        self.assertEqual(back.matched_stake, Decimal('100'))
        self.assertEqual(back.unmatched_stake, Decimal('0'))
        self.assertEqual(ExchangeFill.objects.filter(back_order=back).count(), 2)

    def test_no_match_stays_open(self):
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('3.00'), Decimal('50'))
        order.refresh_from_db()
        self.assertEqual(order.matched_stake, Decimal('0'))
        self.assertEqual(order.status, ExchangeOrder.Status.OPEN)
        self.assertEqual(ExchangeFill.objects.count(), 0)
        # Full stake reserved even though unmatched.
        self.assertEqual(self._reserved(self.backer), Decimal('50'))

    def test_insufficient_balance_rejected(self):
        with self.assertRaises(ValidationError):
            place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100000'))
        self.assertEqual(ExchangeOrder.objects.filter(user=self.backer).count(), 0)

    def test_odds_must_exceed_one(self):
        with self.assertRaises(ValidationError):
            place_order(self.backer, self.match.id, 'home', 'back', Decimal('1.00'), Decimal('10'))

    def test_self_match_excluded(self):
        place_order(self.backer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('50'))
        back = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('50'))
        back.refresh_from_db()
        self.assertEqual(back.matched_stake, Decimal('0'))
        self.assertEqual(ExchangeFill.objects.count(), 0)

    def test_cancel_unmatched_releases_exposure(self):
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('50'))
        self.assertEqual(self._reserved(self.backer), Decimal('50'))
        cancel_order(self.backer, order.id)
        self.assertEqual(self._reserved(self.backer), Decimal('0'))
        order.refresh_from_db()
        self.assertEqual(order.status, ExchangeOrder.Status.CANCELLED)

    def test_cancel_fully_matched_order_rejected(self):
        back = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('50'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('50'))
        with self.assertRaises(ValidationError):
            cancel_order(self.backer, back.id)


class ExchangeSettlementTests(TestCase):
    def setUp(self):
        ExchangeConfig.objects.filter(pk=1).delete()
        self.config = ExchangeConfig.objects.create(pk=1, commission_rate=Decimal('5.00'))

        self.sport = Sport.objects.create(name='ExchangeSettleSport', slug='exchange-settle-sport')
        self.match = Match.objects.create(
            sport=self.sport, home_team='H', away_team='A',
            start_time=timezone.now() + timezone.timedelta(days=1),
        )
        self.backer = User.objects.create_user(email='sbacker@example.com', password='testpass123')
        self.layer = User.objects.create_user(email='slayer@example.com', password='testpass123')
        for u in (self.backer, self.layer):
            Wallet.objects.get_or_create(user=u)
            deposit_funds(u, Decimal('1000'))

    def _balance(self, user):
        return Wallet.objects.get(user=user).balance

    def _reserved(self, user):
        return Wallet.objects.get(user=user).reserved_balance

    def test_back_wins_settlement_math(self):
        # Back 100 @ 2.00 vs Lay 100 @ 2.00. Home wins -> backer wins.
        place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))

        backer_balance_before = self._balance(self.backer)
        layer_balance_before = self._balance(self.layer)

        self.match.status = Match.Status.FINISHED
        self.match.home_score = 1
        self.match.away_score = 0
        self.match.save()
        settle_exchange_for_match(self.match)

        # profit = 100*(2-1) = 100; commission = 5% of 100 = 5; net = 95
        self.assertEqual(self._balance(self.backer), backer_balance_before + Decimal('95'))
        self.assertEqual(self._balance(self.layer), layer_balance_before - Decimal('100'))
        self.assertEqual(self._reserved(self.backer), Decimal('0'))
        self.assertEqual(self._reserved(self.layer), Decimal('0'))

        fill = ExchangeFill.objects.get()
        self.assertEqual(fill.status, ExchangeFill.Status.BACK_WON)
        self.assertEqual(fill.commission_amount, Decimal('5.00000000'))

    def test_lay_wins_settlement_math(self):
        place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))

        backer_balance_before = self._balance(self.backer)
        layer_balance_before = self._balance(self.layer)

        self.match.status = Match.Status.FINISHED
        self.match.home_score = 0
        self.match.away_score = 1
        self.match.save()
        settle_exchange_for_match(self.match)

        # layer keeps backer's stake (100), commission 5% -> net 95
        self.assertEqual(self._balance(self.layer), layer_balance_before + Decimal('95'))
        self.assertEqual(self._balance(self.backer), backer_balance_before - Decimal('100'))
        self.assertEqual(self._reserved(self.backer), Decimal('0'))
        self.assertEqual(self._reserved(self.layer), Decimal('0'))

        fill = ExchangeFill.objects.get()
        self.assertEqual(fill.status, ExchangeFill.Status.LAY_WON)

    def test_cancelled_match_voids_with_no_money_movement(self):
        place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))

        backer_balance_before = self._balance(self.backer)
        layer_balance_before = self._balance(self.layer)

        self.match.status = Match.Status.CANCELLED
        self.match.save()
        settle_exchange_for_match(self.match)

        self.assertEqual(self._balance(self.backer), backer_balance_before)
        self.assertEqual(self._balance(self.layer), layer_balance_before)
        self.assertEqual(self._reserved(self.backer), Decimal('0'))
        self.assertEqual(self._reserved(self.layer), Decimal('0'))
        fill = ExchangeFill.objects.get()
        self.assertEqual(fill.status, ExchangeFill.Status.VOID)

    def test_unmatched_remainder_released_at_settlement(self):
        # Backer's order only half-matched; the other half was never matched.
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('40'))
        back = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        self.assertEqual(self._reserved(self.backer), Decimal('100'))  # full stake reserved upfront

        self.match.status = Match.Status.FINISHED
        self.match.home_score = 1
        self.match.away_score = 0
        self.match.save()
        settle_exchange_for_match(self.match)

        back.refresh_from_db()
        self.assertEqual(back.status, ExchangeOrder.Status.SETTLED)
        # Matched 40 won (net 38 after 5% commission); unmatched 60 exposure released with no effect.
        self.assertEqual(self._reserved(self.backer), Decimal('0'))

    def test_settlement_is_idempotent(self):
        place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))

        self.match.status = Match.Status.FINISHED
        self.match.home_score = 1
        self.match.away_score = 0
        self.match.save()
        settle_exchange_for_match(self.match)
        balance_after_first = self._balance(self.backer)

        settle_exchange_for_match(self.match)  # calling again must not double-pay
        self.assertEqual(self._balance(self.backer), balance_after_first)


class ExchangeCashOutTests(TestCase):
    def setUp(self):
        ExchangeConfig.objects.filter(pk=1).delete()
        self.config = ExchangeConfig.objects.create(pk=1, commission_rate=Decimal('5.00'))

        self.sport = Sport.objects.create(name='CashOutTestSport', slug='cashout-test-sport')
        self.match = Match.objects.create(
            sport=self.sport, home_team='H', away_team='A',
            start_time=timezone.now() + timezone.timedelta(days=1),
        )
        self.backer = User.objects.create_user(email='cobacker@example.com', password='testpass123')
        self.layer = User.objects.create_user(email='colayer@example.com', password='testpass123')
        for u in (self.backer, self.layer):
            Wallet.objects.get_or_create(user=u)
            deposit_funds(u, Decimal('1000'))

    def _make_user(self, email, deposit=Decimal('1000')):
        u = User.objects.create_user(email=email, password='testpass123')
        Wallet.objects.get_or_create(user=u)
        deposit_funds(u, deposit)
        return u

    def _balance(self, user):
        return Wallet.objects.get(user=user).balance

    def _reserved(self, user):
        return Wallet.objects.get(user=user).reserved_balance

    def test_full_cash_out_at_unchanged_price_costs_only_commission(self):
        # Backer's original position is fully offset at the SAME price it
        # was struck at - gross P&L is exactly zero either way, so the only
        # real cost is the commission on whichever leg happens to win.
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))
        counterparty = self._make_user('cocounter1@example.com')
        place_order(counterparty, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))

        result = cash_out_order(self.backer, order.id)
        self.assertEqual(result['status'], 'full')
        self.assertEqual(result['matched_amount'], Decimal('100'))
        self.assertEqual(result['estimated_value'], Decimal('0'))
        self.assertEqual(self._reserved(self.backer), Decimal('200'))  # 100 original + 100 new leg

    def test_full_cash_out_settlement_home_wins(self):
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))
        counterparty = self._make_user('cocounter2@example.com')
        place_order(counterparty, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        cash_out_order(self.backer, order.id)

        balance_before = self._balance(self.backer)
        self.match.status = Match.Status.FINISHED
        self.match.home_score, self.match.away_score = 1, 0
        self.match.save()
        settle_exchange_for_match(self.match)

        # At an unchanged price, cash-out costs exactly the commission on
        # the position (5% of the 100 gross swing), regardless of result.
        self.assertEqual(self._balance(self.backer), balance_before - Decimal('5'))
        self.assertEqual(self._reserved(self.backer), Decimal('0'))

    def test_full_cash_out_settlement_home_does_not_win(self):
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))
        counterparty = self._make_user('cocounter3@example.com')
        place_order(counterparty, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        cash_out_order(self.backer, order.id)

        balance_before = self._balance(self.backer)
        self.match.status = Match.Status.FINISHED
        self.match.home_score, self.match.away_score = 0, 1
        self.match.save()
        settle_exchange_for_match(self.match)

        self.assertEqual(self._balance(self.backer), balance_before - Decimal('5'))
        self.assertEqual(self._reserved(self.backer), Decimal('0'))

    def test_full_cash_out_walks_multiple_price_levels_without_overshoot(self):
        # Original: back 200 @ 2.00 (matched). Target notional = 200 + 200 = 400.
        # Liquidity is spread across two price levels; the walk must land
        # EXACTLY on 400 (taking only a fraction of the second level), not
        # overshoot it - overshooting would break the equal-P&L guarantee.
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('200'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('200'))
        c1 = self._make_user('cocounter4@example.com')
        c2 = self._make_user('cocounter5@example.com')
        place_order(c1, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(c2, self.match.id, 'home', 'back', Decimal('2.50'), Decimal('300'))

        result = cash_out_order(self.backer, order.id)
        self.assertEqual(result['status'], 'full')
        # 100 @ 2.00 (=200 notional) + 80 @ 2.50 (=200 notional) = 400 exactly.
        self.assertEqual(result['matched_amount'], Decimal('180.00000000'))
        self.assertEqual(result['estimated_value'], Decimal('-20.00000000'))

        # c2's resting order must be left with the untaken remainder, not
        # fully consumed - proof the walk took an exact fraction, not the
        # whole level.
        c2_order = ExchangeOrder.objects.get(user=c2)
        self.assertEqual(c2_order.matched_stake, Decimal('80.00000000'))
        self.assertEqual(c2_order.unmatched_stake, Decimal('220.00000000'))
        self.assertEqual(c2_order.status, ExchangeOrder.Status.OPEN)

    def test_multi_level_cash_out_settlement_proves_exact_commission_residual(self):
        # Concrete proof that once the walk lands exactly on target (no
        # overshoot), the only remaining difference between the two possible
        # settlement outcomes is the small, structural per-fill commission
        # residual documented in Section 11 rule 20 - not a gross-math bug.
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('200'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('200'))
        c1 = self._make_user('cocounter6@example.com')
        c2 = self._make_user('cocounter7@example.com')
        place_order(c1, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(c2, self.match.id, 'home', 'back', Decimal('2.50'), Decimal('300'))
        cash_out_order(self.backer, order.id)

        balance_before = self._balance(self.backer)
        self.match.status = Match.Status.FINISHED
        self.match.home_score, self.match.away_score = 1, 0
        self.match.save()
        settle_exchange_for_match(self.match)
        balance_after_win = self._balance(self.backer) - balance_before

        # Reset a fresh identical scenario to compare against the opposite result.
        self.match2 = Match.objects.create(
            sport=self.sport, home_team='H2', away_team='A2',
            start_time=timezone.now() + timezone.timedelta(days=1),
        )
        order2 = place_order(self.backer, self.match2.id, 'home', 'back', Decimal('2.00'), Decimal('200'))
        place_order(self.layer, self.match2.id, 'home', 'lay', Decimal('2.00'), Decimal('200'))
        c3 = self._make_user('cocounter8@example.com')
        c4 = self._make_user('cocounter9@example.com')
        place_order(c3, self.match2.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(c4, self.match2.id, 'home', 'back', Decimal('2.50'), Decimal('300'))
        cash_out_order(self.backer, order2.id)

        balance_before2 = self._balance(self.backer)
        self.match2.status = Match.Status.FINISHED
        self.match2.home_score, self.match2.away_score = 0, 1
        self.match2.save()
        settle_exchange_for_match(self.match2)
        balance_after_lose = self._balance(self.backer) - balance_before2

        self.assertEqual(balance_after_win, Decimal('-30.00000000'))
        self.assertEqual(balance_after_lose, Decimal('-29.00000000'))
        # commission_rate * (new_leg_stake - original_profit) = 0.05*(180-200) = -1
        self.assertEqual(balance_after_win - balance_after_lose, Decimal('-1.00000000'))

    def test_partial_cash_out_when_liquidity_runs_out(self):
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))
        counterparty = self._make_user('cocounter10@example.com')
        place_order(counterparty, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('50'))  # only half enough

        result = cash_out_order(self.backer, order.id)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['matched_amount'], Decimal('50'))
        self.assertIsNone(result['estimated_value'])
        # Original position is untouched - still fully live.
        order.refresh_from_db()
        self.assertEqual(order.matched_stake, Decimal('100'))
        self.assertEqual(order.status, ExchangeOrder.Status.OPEN)
        self.assertEqual(self._reserved(self.backer), Decimal('150'))  # 100 original + 50 new leg

    def test_no_liquidity_makes_no_changes(self):
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))

        result = cash_out_order(self.backer, order.id)
        self.assertEqual(result['status'], 'no_liquidity')
        self.assertEqual(self._reserved(self.backer), Decimal('100'))  # unchanged
        self.assertEqual(ExchangeOrder.objects.filter(user=self.backer).count(), 1)  # no new order created

    def test_cash_out_excludes_own_resting_orders_as_liquidity(self):
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))
        # The only other "back" liquidity is the backer's own second order -
        # must not be matched against themselves.
        place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('500'))

        result = cash_out_order(self.backer, order.id)
        self.assertEqual(result['status'], 'no_liquidity')

    def test_cash_out_capped_by_available_wallet_balance(self):
        backer = self._make_user('cowalletcap@example.com', deposit=Decimal('145'))
        order = place_order(backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))
        # Reserved 100, available 45. Only liquidity is at a steep odds swing
        # (10.00), which would need 20 stake / 180 exposure to fully close -
        # far more than the 45 available - so the wallet caps it down.
        counterparty = self._make_user('cocounter11@example.com')
        place_order(counterparty, self.match.id, 'home', 'back', Decimal('10.00'), Decimal('1000'))

        result = cash_out_order(backer, order.id)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['matched_amount'], Decimal('5.00000000'))
        self.assertEqual(self._reserved(backer), Decimal('145'))  # wallet fully committed, none left available
        self.assertEqual(Wallet.objects.get(user=backer).available_balance, Decimal('0'))

    def test_cash_out_rejects_order_with_nothing_matched(self):
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('3.00'), Decimal('50'))  # no counterparty
        with self.assertRaises(ValidationError):
            cash_out_order(self.backer, order.id)

    def test_cash_out_rejects_wrong_owner(self):
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))
        with self.assertRaises(ValidationError):
            cash_out_order(self.layer, order.id)

    def test_cash_out_rejects_already_cancelled_order(self):
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('50'))
        cancel_order(self.backer, order.id)
        with self.assertRaises(ValidationError):
            cash_out_order(self.backer, order.id)

    def test_cash_out_rejects_after_match_finished(self):
        order = place_order(self.backer, self.match.id, 'home', 'back', Decimal('2.00'), Decimal('100'))
        place_order(self.layer, self.match.id, 'home', 'lay', Decimal('2.00'), Decimal('100'))
        self.match.status = Match.Status.FINISHED
        self.match.home_score, self.match.away_score = 1, 0
        self.match.save()
        with self.assertRaises(ValidationError):
            cash_out_order(self.backer, order.id)
