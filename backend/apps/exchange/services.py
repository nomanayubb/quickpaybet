from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.bets.models import Bet
from apps.sports.models import Match
from apps.wallet.models import Wallet, WalletTransaction

from .models import ExchangeConfig, ExchangeFill, ExchangeOrder

MIN_ODDS = Decimal('1.00')


def _exposure_rate(side: str, odds: Decimal) -> Decimal:
    """
    Reserved amount per unit of stake for one side of an order:
      - back: 1 (you can only ever lose your stake)
      - lay:  (odds - 1) (your liability if the backed outcome happens)
    Always computed from the ORDER's own requested odds, never a fill's
    execution odds - see settle_exchange_for_match() for why this matters:
    it's what keeps reserved_balance releases exactly balanced to what was
    reserved, even when a fill executes at a better ("improved") price than
    the order asked for.
    """
    if side == ExchangeOrder.Side.LAY:
        return odds - 1
    return Decimal('1')


def place_order(user, match_id: int, selection: str, side: str, odds: Decimal, stake: Decimal) -> ExchangeOrder:
    """
    Places a back or lay order and immediately tries to match it against the
    open order book (price-time priority, executes at the resting order's
    price). Any unmatched remainder stays open in the book for others to
    match against later.
    """
    if odds <= MIN_ODDS:
        raise ValidationError('Odds must be greater than 1.00.')
    if stake <= 0:
        raise ValidationError('Stake must be positive.')
    if selection not in Bet.Selection.values:
        raise ValidationError('Invalid selection.')
    if side not in ExchangeOrder.Side.values:
        raise ValidationError('Invalid side.')

    try:
        match = Match.objects.get(pk=match_id)
    except Match.DoesNotExist:
        raise ValidationError('Match not found.')
    if match.status not in (Match.Status.SCHEDULED, Match.Status.LIVE):
        raise ValidationError('Exchange betting is closed for this match.')

    exposure = stake * _exposure_rate(side, odds)

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=user)
        if wallet.available_balance < exposure:
            raise ValidationError('Insufficient available balance for this order.')
        wallet.reserved_balance += exposure
        wallet.save(update_fields=['reserved_balance', 'updated_at'])

        order = ExchangeOrder.objects.create(
            user=user,
            match=match,
            selection=selection,
            side=side,
            odds=odds,
            stake=stake,
        )

        _match_order(order)

    return order


def _match_order(order: ExchangeOrder) -> None:
    """
    Must be called inside an atomic() block. Matches `order` against
    compatible resting orders already in the book, oldest/best-priced first.
    Self-matching (a user matching against their own resting order) is
    excluded deliberately - allowing it would let a user "trade" with
    themselves for no real economic reason beyond manipulating apparent
    activity or paying themselves a commission-reduced amount.
    """
    opposite_side = (
        ExchangeOrder.Side.LAY if order.side == ExchangeOrder.Side.BACK else ExchangeOrder.Side.BACK
    )

    resting_qs = ExchangeOrder.objects.select_for_update().filter(
        match=order.match,
        selection=order.selection,
        side=opposite_side,
        status=ExchangeOrder.Status.OPEN,
    ).exclude(user=order.user)

    if order.side == ExchangeOrder.Side.BACK:
        # A back order wants odds >= its own price; any resting lay offering
        # at least that much is a valid match, best (highest) price first.
        resting_qs = resting_qs.filter(odds__gte=order.odds).order_by('-odds', 'created_at')
    else:
        # A lay order wants odds <= its own price; any resting back asking
        # for that much or less is a valid match, best (lowest) price first.
        resting_qs = resting_qs.filter(odds__lte=order.odds).order_by('odds', 'created_at')

    for resting in resting_qs:
        if order.unmatched_stake <= 0:
            break
        if resting.unmatched_stake <= 0:
            continue

        fill_stake = min(order.unmatched_stake, resting.unmatched_stake)
        execution_odds = resting.odds  # always the resting order's price

        if order.side == ExchangeOrder.Side.BACK:
            back_order, lay_order = order, resting
        else:
            back_order, lay_order = resting, order

        ExchangeFill.objects.create(
            match=order.match,
            selection=order.selection,
            back_order=back_order,
            lay_order=lay_order,
            odds=execution_odds,
            stake=fill_stake,
        )

        order.matched_stake += fill_stake
        resting.matched_stake += fill_stake
        resting.save(update_fields=['matched_stake', 'updated_at'])

    order.save(update_fields=['matched_stake', 'updated_at'])


def cancel_order(user, order_id: int) -> ExchangeOrder:
    """Cancels only the unmatched remainder of an order; already-matched fills stand and settle normally."""
    with transaction.atomic():
        try:
            order = ExchangeOrder.objects.select_for_update().get(pk=order_id, user=user)
        except ExchangeOrder.DoesNotExist:
            raise ValidationError('Order not found.')

        if order.status != ExchangeOrder.Status.OPEN:
            raise ValidationError('This order cannot be cancelled.')
        remainder = order.unmatched_stake
        if remainder <= 0:
            raise ValidationError('This order is already fully matched - nothing to cancel.')

        release_amount = remainder * _exposure_rate(order.side, order.odds)

        wallet = Wallet.objects.select_for_update().get(user=user)
        wallet.reserved_balance -= release_amount
        wallet.save(update_fields=['reserved_balance', 'updated_at'])

        order.status = ExchangeOrder.Status.CANCELLED
        order.save(update_fields=['status', 'updated_at'])

    return order


def _settle_fill(fill_id: int, result: str, commission_rate: Decimal) -> None:
    """
    Settles exactly one fill: releases both sides' reservation (based on
    each order's OWN requested odds - see _exposure_rate) and moves the
    actual win/loss money (based on the fill's real execution odds) between
    the two matched users' wallets, in one atomic transaction locking both
    wallets in a consistent order (by user id) to avoid deadlocking against
    another settlement touching the same pair of users concurrently.
    """
    with transaction.atomic():
        fill = ExchangeFill.objects.select_for_update().get(pk=fill_id)
        if fill.status != ExchangeFill.Status.PENDING:
            return  # already settled - safe to call more than once

        back_order = ExchangeOrder.objects.select_related('user').get(pk=fill.back_order_id)
        lay_order = ExchangeOrder.objects.select_related('user').get(pk=fill.lay_order_id)

        user_ids = sorted([back_order.user_id, lay_order.user_id])
        wallets = {
            w.user_id: w
            for w in Wallet.objects.select_for_update().filter(user_id__in=user_ids).order_by('user_id')
        }
        back_wallet = wallets[back_order.user_id]
        lay_wallet = wallets[lay_order.user_id]

        back_won = fill.selection == result
        profit = fill.stake * (fill.odds - 1) if back_won else fill.stake
        commission = (profit * commission_rate / Decimal('100')).quantize(Decimal('0.00000001'))
        net_profit = profit - commission

        back_release = fill.stake * _exposure_rate(ExchangeOrder.Side.BACK, back_order.odds)
        lay_release = fill.stake * _exposure_rate(ExchangeOrder.Side.LAY, lay_order.odds)

        back_wallet.reserved_balance -= back_release
        lay_wallet.reserved_balance -= lay_release

        if back_won:
            back_wallet.balance += net_profit
            lay_wallet.balance -= profit
            back_desc, lay_desc = f'Exchange back win – fill #{fill.id}', f'Exchange lay loss – fill #{fill.id}'
        else:
            lay_wallet.balance += net_profit
            back_wallet.balance -= profit
            back_desc, lay_desc = f'Exchange back loss – fill #{fill.id}', f'Exchange lay win – fill #{fill.id}'

        back_wallet.save(update_fields=['balance', 'reserved_balance', 'updated_at'])
        lay_wallet.save(update_fields=['balance', 'reserved_balance', 'updated_at'])

        WalletTransaction.objects.create(
            wallet=back_wallet,
            txn_type=WalletTransaction.TxnType.EXCHANGE_WON if back_won else WalletTransaction.TxnType.EXCHANGE_LOST,
            amount=net_profit if back_won else -profit,
            status=WalletTransaction.Status.COMPLETED,
            balance_after=back_wallet.balance,
            description=back_desc,
        )
        WalletTransaction.objects.create(
            wallet=lay_wallet,
            txn_type=WalletTransaction.TxnType.EXCHANGE_LOST if back_won else WalletTransaction.TxnType.EXCHANGE_WON,
            amount=-profit if back_won else net_profit,
            status=WalletTransaction.Status.COMPLETED,
            balance_after=lay_wallet.balance,
            description=lay_desc,
        )

        fill.status = ExchangeFill.Status.BACK_WON if back_won else ExchangeFill.Status.LAY_WON
        fill.commission_amount = commission
        fill.save(update_fields=['status', 'commission_amount'])


def _void_fill(fill_id: int) -> None:
    """Match was cancelled: release both sides' reservation for this fill, no money moves."""
    with transaction.atomic():
        fill = ExchangeFill.objects.select_for_update().get(pk=fill_id)
        if fill.status != ExchangeFill.Status.PENDING:
            return

        back_order = ExchangeOrder.objects.get(pk=fill.back_order_id)
        lay_order = ExchangeOrder.objects.get(pk=fill.lay_order_id)

        user_ids = sorted([back_order.user_id, lay_order.user_id])
        wallets = {
            w.user_id: w
            for w in Wallet.objects.select_for_update().filter(user_id__in=user_ids).order_by('user_id')
        }
        wallets[back_order.user_id].reserved_balance -= fill.stake * _exposure_rate(ExchangeOrder.Side.BACK, back_order.odds)
        wallets[lay_order.user_id].reserved_balance -= fill.stake * _exposure_rate(ExchangeOrder.Side.LAY, lay_order.odds)
        for w in wallets.values():
            w.save(update_fields=['reserved_balance', 'updated_at'])

        fill.status = ExchangeFill.Status.VOID
        fill.save(update_fields=['status'])


def get_order_book(match: Match, depth: int = 5) -> dict:
    """
    Aggregated open back/lay prices per selection, for display only (not
    used for matching itself - place_order()/_match_order() always query
    fresh, locked rows at the time of an actual match attempt).
    """
    from django.db.models import Sum

    book = {}
    for selection in Bet.Selection.values:
        book[selection] = {}
        for side in ExchangeOrder.Side.values:
            rows = (
                ExchangeOrder.objects.filter(
                    match=match, selection=selection, side=side, status=ExchangeOrder.Status.OPEN,
                )
                .values('odds')
                .annotate(total_stake=Sum('stake') - Sum('matched_stake'))
                .filter(total_stake__gt=0)
                .order_by('-odds' if side == ExchangeOrder.Side.BACK else 'odds')[:depth]
            )
            book[selection][side] = list(rows)
    return book


def settle_exchange_for_match(match: Match) -> None:
    """
    Mirror of apps.bets.services.settle_bets_for_match(), called from the
    exact same places (sync_results, the admin settle/cancel views, the
    Celery settlement task) so it runs automatically whenever the
    traditional sportsbook settles - no separate scheduling needed.
    """
    if match.status not in (Match.Status.FINISHED, Match.Status.CANCELLED):
        return

    if match.status == Match.Status.CANCELLED:
        fill_ids = list(
            ExchangeFill.objects.filter(match=match, status=ExchangeFill.Status.PENDING).values_list('id', flat=True)
        )
        for fill_id in fill_ids:
            _void_fill(fill_id)
    else:
        if match.home_score is None or match.away_score is None:
            return
        if match.home_score > match.away_score:
            result = Bet.Selection.HOME
        elif match.away_score > match.home_score:
            result = Bet.Selection.AWAY
        else:
            result = Bet.Selection.DRAW

        commission_rate = ExchangeConfig.get_solo().commission_rate
        fill_ids = list(
            ExchangeFill.objects.filter(match=match, status=ExchangeFill.Status.PENDING).values_list('id', flat=True)
        )
        for fill_id in fill_ids:
            _settle_fill(fill_id, result, commission_rate)

    # Any order still OPEN at this point (fully unmatched, or with an
    # unmatched remainder) can never be matched again once the match is
    # done - release that portion's exposure, it was never actually at risk.
    order_ids = list(
        ExchangeOrder.objects.filter(match=match, status=ExchangeOrder.Status.OPEN).values_list('id', flat=True)
    )
    terminal_status = ExchangeOrder.Status.VOID if match.status == Match.Status.CANCELLED else ExchangeOrder.Status.SETTLED
    for order_id in order_ids:
        with transaction.atomic():
            order = ExchangeOrder.objects.select_for_update().get(pk=order_id)
            if order.status != ExchangeOrder.Status.OPEN:
                continue
            remainder = order.unmatched_stake
            if remainder > 0:
                wallet = Wallet.objects.select_for_update().get(user=order.user)
                wallet.reserved_balance -= remainder * _exposure_rate(order.side, order.odds)
                wallet.save(update_fields=['reserved_balance', 'updated_at'])
            order.status = terminal_status
            order.save(update_fields=['status', 'updated_at'])
