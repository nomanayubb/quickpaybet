from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.sports.models import Sport, Match
from apps.wallet.models import Wallet
from apps.wallet.services import deposit_funds

from .models import ExchangeConfig, ExchangeFill, ExchangeOrder
from .services import cancel_order, place_order, settle_exchange_for_match

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
