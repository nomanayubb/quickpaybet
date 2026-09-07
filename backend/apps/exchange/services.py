import logging
from decimal import ROUND_DOWN, Decimal

from django.core.cache import cache
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.bets.models import Bet
from apps.cashback.services import credit_cashback_for_loss, record_wager
from apps.sports.models import HouseLiquidityConfig, Match
from apps.sports.pricing import compute_lay_price, resolve_effective_lay_spread
from apps.wallet.models import Wallet, WalletTransaction

from .models import ExchangeConfig, ExchangeFill, ExchangeOrder

MIN_ODDS = Decimal('1.00')

logger = logging.getLogger(__name__)


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
    if selection == Bet.Selection.DRAW and match.odds_draw is None:
        raise ValidationError('This match has no draw market.')
    if match.status not in (Match.Status.SCHEDULED, Match.Status.LIVE):
        raise ValidationError('Exchange betting is closed for this match.')

    exposure = stake * _exposure_rate(side, odds)

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=user)
        if wallet.available_balance < exposure:
            raise ValidationError('Insufficient available balance for this order.')
        wallet.reserved_balance += exposure
        wallet.save(update_fields=['reserved_balance', 'updated_at'])
        record_wager(wallet, stake)

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

        loser = lay_order if back_won else back_order
        credit_cashback_for_loss(loser.user, profit, f'Exchange fill #{fill.id} loss')


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


def _cash_out_target_notional(order: ExchangeOrder) -> Decimal:
    """
    The Σ(stake*odds) an opposing order must accumulate, walking the book
    best-price-first, to make `order`'s combined P&L identical whether the
    match result favours it or not. Same formula for both sides - the sum
    term is a back order's profit-if-win, or a lay order's liability-if-
    happens, but it's the same arithmetic (stake*(odds-1)) either way.
    See docs/PROJECT_MASTER_DOCUMENTATION rule 20 for the derivation.
    """
    if order.side == ExchangeOrder.Side.BACK:
        fills = ExchangeFill.objects.filter(back_order=order, status=ExchangeFill.Status.PENDING)
    else:
        fills = ExchangeFill.objects.filter(lay_order=order, status=ExchangeFill.Status.PENDING)
    stake_odds_sum = sum((f.stake * (f.odds - 1) for f in fills), Decimal('0'))
    return order.matched_stake + stake_odds_sum


def _resting_book_for_cash_out(order: ExchangeOrder, new_leg_side: str, locked: bool):
    """
    The resting orders a new cash-out leg (side=new_leg_side, always the
    OPPOSITE of order.side) will actually match against - i.e. resting
    orders on `order`'s OWN side (other backers if `order` is a back, other
    layers if `order` is a lay). Uses the exact same filter/ordering
    _match_order() applies for an order of side=new_leg_side, so whatever
    this walks is exactly what will later match. `locked=True` for real
    execution (must be called inside an atomic() block); `locked=False` for
    a read-only preview.
    """
    qs = ExchangeOrder.objects.select_for_update() if locked else ExchangeOrder.objects
    qs = qs.filter(
        match=order.match,
        selection=order.selection,
        side=order.side,
        status=ExchangeOrder.Status.OPEN,
    ).exclude(user=order.user)
    if new_leg_side == ExchangeOrder.Side.LAY:
        # A new lay leg matches resting backs where back.odds <= its own
        # price, best (lowest) price first - see _match_order().
        return qs.order_by('odds', 'created_at')
    return qs.order_by('-odds', 'created_at')


def _simulate_cash_out_walk(target_notional: Decimal, resting_qs) -> dict:
    """
    Walks resting_qs (already correctly filtered/ordered/locked-or-not by
    the caller), taking the full available stake at each price level, until
    a level has more than what's needed - at which point it takes only the
    EXACT fraction of that final level needed to make the accumulated
    notional land precisely on target_notional, never past it.

    This precision matters: overshooting the target (e.g. by taking a whole
    extra level's worth of stake once the target is already exceeded) does
    NOT just give a "better than promised" result - it breaks the equal-P&L
    guarantee the whole cash-out depends on, introducing a real, potentially
    large gross imbalance between the two possible match outcomes (proven
    with concrete numbers in this module's tests). Landing exactly on the
    target keeps the two outcomes equal (before commission - see Section 11
    rule 20 for the smaller, unavoidable commission-only residual).

    The returned `stake` is submitted as one order's total stake;
    _match_order()'s existing min(unmatched, resting.unmatched) partial-fill
    logic naturally consumes the fractional amount from the final level -
    no special-cased partial-fill logic is needed here.
    """
    cumulative_notional = Decimal('0')
    stake_needed = Decimal('0')
    worst_odds = None
    for resting in resting_qs:
        available = resting.unmatched_stake
        if available <= 0:
            continue
        level_notional = available * resting.odds
        if cumulative_notional + level_notional >= target_notional:
            remaining_notional = target_notional - cumulative_notional
            partial_stake = (remaining_notional / resting.odds).quantize(Decimal('0.00000001'))
            return {
                'stake': stake_needed + partial_stake,
                'worst_odds': resting.odds,
                'target_notional': target_notional,
                'reached_target': True,
            }
        cumulative_notional += level_notional
        stake_needed += available
        worst_odds = resting.odds
    return {
        'stake': stake_needed,
        'worst_odds': worst_odds,
        'target_notional': target_notional,
        'reached_target': False,
    }


def _cash_out_guaranteed_value(order: ExchangeOrder, new_leg_stake: Decimal) -> Decimal:
    """
    Estimated net P&L delta a fully-reached cash-out locks in, beyond what's
    already committed. This is the GROSS figure (equal in both outcomes
    before commission) - deliberately called "estimated", not "guaranteed",
    to callers: commission is charged per-fill on whichever bet happens to
    win, not on a user's net result for the match, so once commission is
    applied the two possible real outcomes can differ from this number (and
    from each other) by a small amount whenever the cash-out's two fills
    have different gross sizes (i.e. whenever the market moved between the
    original bet and the cash-out). See Section 11 rule 20. NOT symmetric
    between sides: a BACK order is closed by a LAY leg (value =
    new_leg_stake - order.matched_stake), a LAY order is closed by a BACK
    leg (value = order.matched_stake - new_leg_stake). Can be negative
    (cashing out to cut a loss is a valid, expected use).
    """
    if order.side == ExchangeOrder.Side.BACK:
        return new_leg_stake - order.matched_stake
    return order.matched_stake - new_leg_stake


def preview_cash_out(order: ExchangeOrder) -> dict:
    """
    Read-only estimate for display only - no locks, no mutation, never used
    to actually move money. The real cash_out_order() always recomputes
    fresh at execution time regardless of what this last returned.
    """
    if order.matched_stake <= 0:
        return {'status': 'no_liquidity'}
    opposite_side = ExchangeOrder.Side.LAY if order.side == ExchangeOrder.Side.BACK else ExchangeOrder.Side.BACK
    target_notional = _cash_out_target_notional(order)
    resting_qs = _resting_book_for_cash_out(order, opposite_side, locked=False)
    walk = _simulate_cash_out_walk(target_notional, resting_qs)
    if walk['worst_odds'] is None:
        return {'status': 'no_liquidity'}
    guaranteed_value = _cash_out_guaranteed_value(order, walk['stake']) if walk['reached_target'] else None
    return {
        'status': 'full' if walk['reached_target'] else 'partial',
        'estimated_value': guaranteed_value,
    }


def cash_out_order(user, order_id: int) -> dict:
    """
    Closes out (fully or partially) an order's current matched position by
    placing a real opposing order into the book, sized and priced to make
    the owner's P&L identical regardless of the match result - matched
    against genuine counterparties, never the house. Liquidity-dependent
    like any real exchange: if the book can't fully absorb the needed
    opposing stake, whatever it CAN absorb is cashed out (matching Betfair's
    own front-end behaviour: partial match, immediate-or-cancel of whatever
    doesn't fill) and the rest of the position stays live.
    """
    with transaction.atomic():
        try:
            order = ExchangeOrder.objects.select_for_update().get(pk=order_id, user=user)
        except ExchangeOrder.DoesNotExist:
            raise ValidationError('Order not found.')

        if order.status != ExchangeOrder.Status.OPEN:
            raise ValidationError('This order cannot be cashed out.')
        if order.matched_stake <= 0:
            raise ValidationError('Nothing matched on this order yet.')
        if order.match.status not in (Match.Status.SCHEDULED, Match.Status.LIVE):
            raise ValidationError('This match is no longer open for cash-out.')

        opposite_side = ExchangeOrder.Side.LAY if order.side == ExchangeOrder.Side.BACK else ExchangeOrder.Side.BACK
        target_notional = _cash_out_target_notional(order)

        # Locking these rows now, before walking, guarantees what gets
        # walked is exactly what _match_order() matches a moment later -
        # no race with a concurrent order against the same book.
        resting_qs = _resting_book_for_cash_out(order, opposite_side, locked=True)
        walk = _simulate_cash_out_walk(target_notional, resting_qs)

        if walk['worst_odds'] is None:
            return {'status': 'no_liquidity', 'matched_amount': Decimal('0')}

        stake_needed = walk['stake']
        worst_odds = walk['worst_odds']
        reached_target = walk['reached_target']

        exposure = stake_needed * _exposure_rate(opposite_side, worst_odds)
        wallet = Wallet.objects.select_for_update().get(user=user)
        if wallet.available_balance < exposure:
            # An adverse price move can require MORE exposure than the
            # original position had reserved, not just less - cap to what's
            # actually affordable rather than reject outright, same "give
            # whatever is safely possible" philosophy used for liquidity.
            # Capping here means the full target is no longer reached, even
            # if the book itself had enough liquidity for it.
            affordable_stake = (wallet.available_balance / _exposure_rate(opposite_side, worst_odds)).quantize(Decimal('0.00000001'))
            stake_needed = max(Decimal('0'), min(stake_needed, affordable_stake))
            exposure = stake_needed * _exposure_rate(opposite_side, worst_odds)
            reached_target = False

        if stake_needed <= 0:
            return {'status': 'no_liquidity', 'matched_amount': Decimal('0')}

        wallet.reserved_balance += exposure
        wallet.save(update_fields=['reserved_balance', 'updated_at'])
        record_wager(wallet, stake_needed)

        cash_out_leg = ExchangeOrder.objects.create(
            user=user,
            match=order.match,
            selection=order.selection,
            side=opposite_side,
            odds=worst_odds,
            stake=stake_needed,
        )
        _match_order(cash_out_leg)

        # A cash-out never leaves a new speculative resting order behind -
        # cancel whatever didn't fill and release its exposure immediately.
        leftover = cash_out_leg.unmatched_stake
        if leftover > 0:
            release = leftover * _exposure_rate(opposite_side, worst_odds)
            wallet.reserved_balance -= release
            wallet.save(update_fields=['reserved_balance', 'updated_at'])
            cash_out_leg.status = ExchangeOrder.Status.CANCELLED
            cash_out_leg.save(update_fields=['status', 'updated_at'])

        matched_amount = cash_out_leg.matched_stake
        if matched_amount <= 0:
            status = 'no_liquidity'
        elif matched_amount >= stake_needed and reached_target:
            status = 'full'
        else:
            status = 'partial'

        guaranteed_value = _cash_out_guaranteed_value(order, matched_amount) if status == 'full' else None

        return {
            'status': status,
            'matched_amount': matched_amount,
            'estimated_value': guaranteed_value,
            'order': cash_out_leg,
        }


def get_order_book(match: Match, depth: int = 5) -> dict:
    """
    Aggregated open back/lay prices per selection, for display only (not
    used for matching itself - place_order()/_match_order() always query
    fresh, locked rows at the time of an actual match attempt).
    """
    from django.db.models import Sum

    selections = [Bet.Selection.HOME, Bet.Selection.AWAY]
    if match.odds_draw is not None:
        selections.append(Bet.Selection.DRAW)

    book = {}
    for selection in selections:
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


def get_house_user():
    """
    Returns the single flagged house User (see User.is_house_account, a
    database-enforced at-most-one constraint), or None if an admin hasn't
    designated one yet. Every caller must treat None as "feature not
    configured yet, skip silently" - never a crash - since this is checked
    on every odds refresh, long before any admin necessarily sets this up.
    """
    from apps.accounts.models import User
    return User.objects.filter(is_house_account=True).first()


def _house_reseed_lock_key(match_id: int) -> str:
    return f'house_lay_reseed_lock:{match_id}'


def _house_committed_liability(house_user, match, selection) -> Decimal:
    """
    Real, currently-outstanding house liability on this match+selection:
    every MATCHED stake unit across all the house's own lay orders here
    (any status except VOID - a CANCELLED order's already-matched portion
    is still a live ExchangeFill the house owes on if the selection wins;
    only its unmatched remainder disappeared on cancel). VOID only ever
    happens post-settlement/cancellation, by which point
    sync_house_lay_orders_for_match no longer runs for this match anyway
    (see its own SCHEDULED/LIVE guard below).
    """
    orders = ExchangeOrder.objects.filter(
        user=house_user, match=match, selection=selection, side=ExchangeOrder.Side.LAY,
    ).exclude(status=ExchangeOrder.Status.VOID)
    return sum((o.matched_stake * (o.odds - 1) for o in orders), Decimal('0'))


def sync_house_lay_orders_for_match(match: Match) -> None:
    """
    Called right after match.odds_home/draw/away are (re)written - from
    apps.sports.realtime.maybe_refresh_sport_odds() and
    apps.sports.management.commands.sync_odds._process_match(), the same
    two call sites apply_odds_adjustment() already hooks into. For each
    selection this match actually has (skips 'draw' entirely for a 2-way
    match - no draw lay price or house order can ever be created for one
    by construction), computes the target lay price, cancels the house's
    stale UNMATCHED resting order there if one exists (already-matched
    fills are historical and are never touched), and places one fresh
    house lay order sized to exactly fill whatever cap headroom remains.

    No-ops immediately unless HouseLiquidityConfig.is_enabled, a house
    account has been configured, and the match is still open for betting -
    shipping this code changes nothing until an admin deliberately opts in.
    """
    config = HouseLiquidityConfig.get_solo()
    if not config.is_enabled:
        return
    if match.status not in (Match.Status.SCHEDULED, Match.Status.LIVE):
        return

    house_user = get_house_user()
    if house_user is None:
        return

    if not cache.add(_house_reseed_lock_key(match.id), True, timeout=15):
        # Another reseed pass for this exact match is already in flight
        # (e.g. a scheduled sync_odds run landing at the same moment as a
        # viewer-triggered refresh) - skip rather than double-offer
        # liquidity. Same cache.add()-based atomic-lock idiom as
        # maybe_refresh_sport_odds's per-sport lock, scoped to one match.
        # The underlying wallet arithmetic in place_order()/cancel_order()
        # is already safely serialized regardless via select_for_update();
        # this lock's job is purely to stop the house from briefly
        # offering more than its own configured cap, not a money-safety
        # backstop by itself.
        return

    effective_spread = resolve_effective_lay_spread(match.lay_spread_override, config.default_lay_spread)
    effective_cap = (
        match.house_max_liability_override
        if match.house_max_liability_override is not None
        else config.default_max_liability_per_selection
    )

    selections = [Bet.Selection.HOME, Bet.Selection.AWAY]
    if match.odds_draw is not None:
        selections.append(Bet.Selection.DRAW)

    back_odds_by_selection = {
        Bet.Selection.HOME: match.odds_home,
        Bet.Selection.DRAW: match.odds_draw,
        Bet.Selection.AWAY: match.odds_away,
    }

    for selection in selections:
        back_odds = back_odds_by_selection[selection]
        if back_odds is None:
            continue
        lay_price = compute_lay_price(back_odds, effective_spread)

        with transaction.atomic():
            stale_orders = ExchangeOrder.objects.select_for_update().filter(
                user=house_user, match=match, selection=selection,
                side=ExchangeOrder.Side.LAY, status=ExchangeOrder.Status.OPEN,
            )
            for stale in stale_orders:
                if stale.unmatched_stake > 0:
                    cancel_order(house_user, stale.id)

        committed = _house_committed_liability(house_user, match, selection)
        headroom = effective_cap - committed
        if headroom <= 0:
            continue

        stake_to_offer = (headroom / (lay_price - 1)).quantize(
            Decimal('0.00000001'), rounding=ROUND_DOWN,
        )
        if stake_to_offer <= 0:
            continue

        try:
            place_order(
                user=house_user, match_id=match.id, selection=selection,
                side=ExchangeOrder.Side.LAY, odds=lay_price, stake=stake_to_offer,
            )
        except ValidationError as exc:
            logger.warning(
                'House lay reseed skipped for match %s selection %s: %s', match.id, selection, exc,
            )
