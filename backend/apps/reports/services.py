from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum, Count
from django.utils import timezone

from apps.accounts.models import User
from apps.bets.models import Bet
from apps.wallet.models import WalletTransaction


def get_overview_report():
    total_users = User.objects.count()
    total_bets = Bet.objects.count()
    total_turnover = Bet.objects.aggregate(total=Sum('stake'))['total'] or Decimal('0')
    total_payout = Bet.objects.filter(status=Bet.Status.WON).aggregate(
        total=Sum('potential_payout')
    )['total'] or Decimal('0')
    total_refunds = Bet.objects.filter(status=Bet.Status.REFUNDED).aggregate(
        total=Sum('stake')
    )['total'] or Decimal('0')
    total_deposits = WalletTransaction.objects.filter(
        txn_type=WalletTransaction.TxnType.DEPOSIT,
        status=WalletTransaction.Status.COMPLETED
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_withdrawals = WalletTransaction.objects.filter(
        txn_type=WalletTransaction.TxnType.WITHDRAWAL,
        status=WalletTransaction.Status.COMPLETED
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

    return {
        'total_users': total_users,
        'total_bets': total_bets,
        'total_turnover': str(total_turnover),
        'total_payout': str(total_payout),
        'total_refunds': str(total_refunds),
        'total_deposits': str(total_deposits),
        'total_withdrawals': str(total_withdrawals),
        'profit': str(total_turnover - total_payout - total_refunds),
    }


def get_daily_report(start_date=None, end_date=None):
    today = timezone.now().date()
    if start_date is None:
        start_date = today - timedelta(days=30)
    if end_date is None:
        end_date = today

    daily = []
    day = start_date
    while day <= end_date:
        day_bets = Bet.objects.filter(created_at__date=day)
        total_turnover = day_bets.aggregate(
            total=Sum('stake')
        )['total'] or Decimal('0')
        total_payout = day_bets.filter(status=Bet.Status.WON).aggregate(
            total=Sum('potential_payout')
        )['total'] or Decimal('0')
        daily.append({
            'date': day.isoformat(),
            'bets_count': day_bets.count(),
            'turnover': str(total_turnover),
            'payout': str(total_payout),
            'profit': str(total_turnover - total_payout),
        })
        day += timedelta(days=1)
    return daily


def get_sport_report():
    """Aggregate betting metrics per individual sport."""
    from django.db.models.functions import Coalesce
    from apps.sports.models import Sport

    rows = []
    for sport in Sport.objects.annotate(
        sport_bets=Count('matches__bets'),
        sport_turnover=Coalesce(Sum('matches__bets__stake'), Decimal('0.0')),
    ):
        payout = Bet.objects.filter(
            match__sport=sport,
            status=Bet.Status.WON,
        ).aggregate(total=Coalesce(Sum('potential_payout'), Decimal('0.0')))['total']
        rows.append({
            'name': sport.name,
            'bets_count': sport.sport_bets,
            'turnover': str(sport.sport_turnover),
            'payout': str(payout),
            'profit': str(sport.sport_turnover - payout),
        })
    return rows


def get_user_report():
    """Aggregate per-user betting totals (top by volume)."""
    from django.db.models import Count

    rows = []
    for user in User.objects.annotate(user_bets=Count('bets')).order_by('-user_bets')[:20]:
        turnover = Bet.objects.filter(user=user).aggregate(total=Sum('stake'))['total'] or Decimal('0')
        payout = Bet.objects.filter(user=user, status=Bet.Status.WON).aggregate(
            total=Sum('potential_payout')
        )['total'] or Decimal('0')
        rows.append({
            'email': user.email,
            'bets_count': user.user_bets,
            'turnover': str(turnover),
            'payout': str(payout),
            'profit': str(turnover - payout),
        })
    return rows


def get_match_report():
    """Aggregate betting totals per match (recently started matches)."""
    from django.db.models import Count

    rows = []
    for match in Bet.objects.values('match').annotate(bets_count=Count('id')).order_by('-bets_count')[:30]:
        match_obj = Bet.objects.filter(match_id=match['match']).first().match
        turnover = Bet.objects.filter(match_id=match['match']).aggregate(total=Sum('stake'))['total'] or Decimal('0')
        payout = Bet.objects.filter(match_id=match['match'], status=Bet.Status.WON).aggregate(
            total=Sum('potential_payout')
        )['total'] or Decimal('0')
        rows.append({
            'label': f"{match_obj.home_team} v {match_obj.away_team}",
            'bets_count': match['bets_count'],
            'turnover': str(turnover),
            'payout': str(payout),
            'profit': str(turnover - payout),
        })
    return rows
