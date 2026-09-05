from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.sports.models import Match
from apps.wallet.models import Wallet, WalletTransaction
from .models import Bet


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
    """Settle all pending bets for a finished match."""
    if match.status != Match.Status.FINISHED:
        return

    if match.home_score is None or match.away_score is None:
        return

    if match.home_score > match.away_score:
        match_result = Bet.Selection.HOME
    elif match.away_score > match.home_score:
        match_result = Bet.Selection.AWAY
    else:
        match_result = Bet.Selection.DRAW

    pending_bets = Bet.objects.select_for_update().filter(
        match=match,
        status=Bet.Status.PENDING,
    )

    for bet in pending_bets:
        with transaction.atomic():
            settle_bet(bet, match_result)
