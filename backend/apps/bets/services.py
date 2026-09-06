from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.sports.models import Match
from apps.wallet.models import Wallet, WalletTransaction
from .models import Bet, ParlayBet, ParlayLeg

# ParlayBet.total_odds is a DecimalField(max_digits=10, decimal_places=2),
# i.e. a hard ceiling of 99,999,999.99. Multiplying many legs' odds together
# can exceed that easily (confirmed: 30 legs at 2.00 odds already overflows
# by several orders of magnitude). Cap both the leg count and the resulting
# combined odds so this is rejected with a clear message instead of either
# silently storing a wrong value (SQLite) or crashing with a database
# overflow error (PostgreSQL).
MAX_PARLAY_LEGS = 12
MAX_COMBINED_ODDS = Decimal('999999.99')


def _get_bet_odds(match: Match, selection: str) -> Decimal:
    odds_map = {
        Bet.Selection.HOME: match.odds_home,
        Bet.Selection.DRAW: match.odds_draw,
        Bet.Selection.AWAY: match.odds_away,
    }
    odds = odds_map.get(selection)
    if odds is None:
        raise ValidationError('Match does not have odds for the selected outcome.')
    return odds


def place_bet(user, match_id: int, selection: str, stake: Decimal) -> Bet:
    if stake <= 0:
        raise ValidationError('Stake must be positive.')

    if not user.is_betting_enabled:
        raise ValidationError('Betting is disabled for your account.')

    if user.min_bet_amount is not None and stake < user.min_bet_amount:
        raise ValidationError(
            f'Minimum stake allowed is {user.min_bet_amount}.'
        )
    if user.max_bet_amount is not None and stake > user.max_bet_amount:
        raise ValidationError(
            f'Maximum stake allowed is {user.max_bet_amount}.'
        )

    try:
        match = Match.objects.get(pk=match_id)
    except Match.DoesNotExist:
        raise ValidationError('Match not found.')

    if match.status not in (Match.Status.SCHEDULED, Match.Status.LIVE):
        raise ValidationError('Betting is closed for this match.')

    odds = _get_bet_odds(match, selection)

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=user)
        if wallet.balance < stake:
            raise ValidationError('Insufficient balance.')

        wallet.balance -= stake
        wallet.save(update_fields=['balance', 'updated_at'])

        WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.TxnType.BET_PLACED,
            amount=-stake,
            status=WalletTransaction.Status.COMPLETED,
            balance_after=wallet.balance,
            description=f'Bet placed on {match.home_team} v {match.away_team}',
        )

        bet = Bet.objects.create(
            user=user,
            match=match,
            selection=selection,
            odds=odds,
            stake=stake,
            potential_payout=stake * odds,
        )

    return bet


def _credit_affiliate_commission(bet: Bet):
    """
    Credit the direct parent a percentage of a losing single-bet stake.

    Only called when a single bet is settled as LOST.
    """
    user = bet.user
    parent = user.parent
    if parent is None:
        return

    rate = parent.commission_rate
    if rate is None or rate <= 0:
        return

    commission = (bet.stake * rate) / Decimal('100')
    if commission <= 0:
        return

    try:
        parent_wallet = Wallet.objects.select_for_update().get(user=parent)
    except Wallet.DoesNotExist:
        return

    parent_wallet.balance += commission
    parent_wallet.save(update_fields=['balance', 'updated_at'])

    WalletTransaction.objects.create(
        wallet=parent_wallet,
        txn_type=WalletTransaction.TxnType.COMMISSION,
        amount=commission,
        status=WalletTransaction.Status.COMPLETED,
        balance_after=parent_wallet.balance,
        description=f'Commission on losing bet #{bet.id} from {user.email}',
    )


def _credit_affiliate_commission_on_parlay(parlay: ParlayBet):
    """
    Credit the direct parent a percentage of the parlay stake when a
    parlay bet settles as LOST.
    """
    user = parlay.user
    parent = user.parent
    if parent is None:
        return

    rate = parent.commission_rate
    if rate is None or rate <= 0:
        return

    commission = (parlay.stake * rate) / Decimal('100')
    if commission <= 0:
        return

    try:
        parent_wallet = Wallet.objects.select_for_update().get(user=parent)
    except Wallet.DoesNotExist:
        return

    parent_wallet.balance += commission
    parent_wallet.save(update_fields=['balance', 'updated_at'])

    WalletTransaction.objects.create(
        wallet=parent_wallet,
        txn_type=WalletTransaction.TxnType.COMMISSION,
        amount=commission,
        status=WalletTransaction.Status.COMPLETED,
        balance_after=parent_wallet.balance,
        description=f'Commission on losing parlay #{parlay.id} from {user.email}',
    )


def place_parlay_bet(user, stake: Decimal, selections: list):
    """
    selections : list of dicts -> [{'match': int, 'selection': 'home'}, ...]
    """
    if stake <= 0:
        raise ValidationError('Stake must be positive.')

    if not user.is_betting_enabled:
        raise ValidationError('Betting is disabled for your account.')

    if user.min_bet_amount is not None and stake < user.min_bet_amount:
        raise ValidationError(
            f'Minimum stake allowed is {user.min_bet_amount}.'
        )
    if user.max_bet_amount is not None and stake > user.max_bet_amount:
        raise ValidationError(
            f'Maximum stake allowed is {user.max_bet_amount}.'
        )

    if len(selections) < 2:
        raise ValidationError('A parlay bet requires at least two selections.')
    if len(selections) > MAX_PARLAY_LEGS:
        raise ValidationError(f'A parlay bet cannot have more than {MAX_PARLAY_LEGS} selections.')

    validated_legs = []
    combined_odds = Decimal('1.00')
    seen_match_ids = set()
    for item in selections:
        match_id = item.get('match')
        selection = item.get('selection')
        if not match_id or selection not in Bet.Selection.values:
            raise ValidationError('Invalid parlay selection.')

        try:
            match = Match.objects.get(pk=match_id)
        except Match.DoesNotExist:
            raise ValidationError('Match not found.')

        if match.id in seen_match_ids:
            raise ValidationError('Same match cannot appear twice in one parlay.')
        seen_match_ids.add(match.id)

        if match.status not in (Match.Status.SCHEDULED, Match.Status.LIVE):
            raise ValidationError('Betting is closed for this match.')

        odds = _get_bet_odds(match, selection)
        combined_odds *= odds
        if combined_odds > MAX_COMBINED_ODDS:
            # Belt-and-braces on top of MAX_PARLAY_LEGS: a handful of
            # high-odds legs can overflow ParlayBet.total_odds
            # (max_digits=10) just as easily as many low-odds ones would.
            # PostgreSQL raises a hard DataError on overflow; SQLite silently
            # truncates/stores a wrong value instead - neither is acceptable
            # for a bet amount, so this is checked before anything is saved.
            raise ValidationError(
                'Combined odds for this parlay are too high to place.'
            )
        validated_legs.append((match, selection, odds))

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=user)
        if wallet.balance < stake:
            raise ValidationError('Insufficient balance.')

        wallet.balance -= stake
        wallet.save(update_fields=['balance', 'updated_at'])

        WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.TxnType.BET_PLACED,
            amount=-stake,
            status=WalletTransaction.Status.COMPLETED,
            balance_after=wallet.balance,
            description=f'Parlay bet placed ({len(validated_legs)} legs)',
        )

        parlay = ParlayBet.objects.create(
            user=user,
            stake=stake,
            total_odds=combined_odds,
            potential_payout=stake * combined_odds,
            status=ParlayBet.Status.PENDING,
        )

        for match, selection, odds in validated_legs:
            ParlayLeg.objects.create(
                parlay=parlay,
                match=match,
                selection=selection,
                odds=odds,
            )

    return parlay


def _settle_parlay_if_ready(parlay_id: int):
    with transaction.atomic():
        parlay = ParlayBet.objects.select_for_update().get(pk=parlay_id)
        if parlay.status != ParlayBet.Status.PENDING:
            return

        legs = list(parlay.legs.all())
        pending = any(leg.outcome == ParlayLeg.Outcome.PENDING for leg in legs)
        if pending:
            return

        has_lost = any(leg.outcome == ParlayLeg.Outcome.LOST for leg in legs)
        if has_lost:
            parlay.status = ParlayBet.Status.LOST
            parlay.save(update_fields=['status', 'updated_at'])
            _credit_affiliate_commission_on_parlay(parlay)
            return

        has_refunded = any(leg.outcome == ParlayLeg.Outcome.REFUNDED for leg in legs)
        if has_refunded:
            # Refund full stake when any leg is refunded and no leg has lost.
            wallet = Wallet.objects.select_for_update().get(user=parlay.user)
            wallet.balance += parlay.stake
            wallet.save(update_fields=['balance', 'updated_at'])
            WalletTransaction.objects.create(
                wallet=wallet,
                txn_type=WalletTransaction.TxnType.BET_REFUND,
                amount=parlay.stake,
                status=WalletTransaction.Status.COMPLETED,
                balance_after=wallet.balance,
                description=f'Parlay refund – {parlay.legs.count()} legs',
            )
            parlay.status = ParlayBet.Status.REFUNDED
            parlay.save(update_fields=['status', 'updated_at'])
            return

        all_won = all(leg.outcome == ParlayLeg.Outcome.WON for leg in legs)
        if all_won and len(legs) > 0:
            wallet = Wallet.objects.select_for_update().get(user=parlay.user)
            wallet.balance += parlay.potential_payout
            wallet.save(update_fields=['balance', 'updated_at'])
            WalletTransaction.objects.create(
                wallet=wallet,
                txn_type=WalletTransaction.TxnType.BET_WON,
                amount=parlay.potential_payout,
                status=WalletTransaction.Status.COMPLETED,
                balance_after=wallet.balance,
                description=f'Parlay won – {parlay.legs.count()} legs',
            )
            parlay.status = ParlayBet.Status.WON
            parlay.save(update_fields=['status', 'updated_at'])


def settle_parlays_for_match(match: Match):
    """Settle pending parlay legs for a finished / cancelled match."""
    with transaction.atomic():
        legs = ParlayLeg.objects.select_for_update().filter(
            match=match,
            outcome=ParlayLeg.Outcome.PENDING,
        )
        if not legs.exists():
            return

        affected_parlay_ids = set(legs.values_list('parlay_id', flat=True))

        if match.status == Match.Status.CANCELLED:
            legs.update(outcome=ParlayLeg.Outcome.REFUNDED)
        elif match.status == Match.Status.FINISHED:
            if match.home_score is None or match.away_score is None:
                return
            if match.home_score > match.away_score:
                match_result = Bet.Selection.HOME
            elif match.away_score > match.home_score:
                match_result = Bet.Selection.AWAY
            else:
                match_result = Bet.Selection.DRAW

            for leg in legs:
                if leg.selection == match_result:
                    leg.outcome = ParlayLeg.Outcome.WON
                else:
                    leg.outcome = ParlayLeg.Outcome.LOST
                leg.save(update_fields=['outcome'])

    for parlay_id in affected_parlay_ids:
        _settle_parlay_if_ready(parlay_id)


def settle_bet(bet: Bet, match_result: str):
    """Call inside an atomic block with a locked wallet."""
    if bet.status != Bet.Status.PENDING:
        return

    wallet = Wallet.objects.select_for_update().get(user=bet.user)

    if bet.selection == match_result:
        payout = bet.stake * bet.odds
        wallet.balance += payout
        wallet.save(update_fields=['balance', 'updated_at'])

        WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.TxnType.BET_WON,
            amount=payout,
            status=WalletTransaction.Status.COMPLETED,
            balance_after=wallet.balance,
            description='Winning bet payout',
        )

        bet.status = Bet.Status.WON
        bet.save(update_fields=['status', 'updated_at'])
    else:
        bet.status = Bet.Status.LOST
        bet.save(update_fields=['status', 'updated_at'])
        _credit_affiliate_commission(bet)


def refund_bet(bet: Bet):
    """Refund stake when a match is cancelled."""
    if bet.status != Bet.Status.PENDING:
        return

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=bet.user)
        wallet.balance += bet.stake
        wallet.save(update_fields=['balance', 'updated_at'])

        WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.TxnType.BET_REFUND,
            amount=bet.stake,
            status=WalletTransaction.Status.COMPLETED,
            balance_after=wallet.balance,
            description='Bet refund – match cancelled',
        )

        bet.status = Bet.Status.REFUNDED
        bet.save(update_fields=['status', 'updated_at'])


def settle_bets_for_match(match: Match):
    """Settle all pending bets and parlay legs for a finished/cancelled match."""
    if match.status not in (Match.Status.FINISHED, Match.Status.CANCELLED):
        return

    if match.status == Match.Status.CANCELLED:
        with transaction.atomic():
            pending_bet_ids = list(
                Bet.objects.select_for_update().filter(
                    match=match,
                    status=Bet.Status.PENDING,
                ).values_list('id', flat=True)
            )
        for bet_id in pending_bet_ids:
            with transaction.atomic():
                bet = Bet.objects.select_for_update().get(pk=bet_id)
                refund_bet(bet)

    if match.status == Match.Status.FINISHED and match.home_score is not None and match.away_score is not None:
        if match.home_score > match.away_score:
            match_result = Bet.Selection.HOME
        elif match.away_score > match.home_score:
            match_result = Bet.Selection.AWAY
        else:
            match_result = Bet.Selection.DRAW

        with transaction.atomic():
            pending_bet_ids = list(
                Bet.objects.select_for_update().filter(
                    match=match,
                    status=Bet.Status.PENDING,
                ).values_list('id', flat=True)
            )

        for bet_id in pending_bet_ids:
            with transaction.atomic():
                bet = Bet.objects.select_for_update().get(pk=bet_id)
                settle_bet(bet, match_result)

    settle_parlays_for_match(match)
