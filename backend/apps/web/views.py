from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import authenticate
from django.core.cache import cache
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.db import IntegrityError
from rest_framework.exceptions import ValidationError as ServiceValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from datetime import timedelta

from django.conf import settings
from decimal import Decimal, InvalidOperation

from apps.common.middleware import PAKISTAN_TZ
from apps.accounts.models import User, PasswordResetToken
from apps.accounts.permissions import roles_assignable_by, user_can_manage_target
from apps.audit.models import AuditLog
from apps.audit.services import create_audit_log
from apps.bets.models import Bet, ParlayBet
from apps.bets.services import place_bet, place_parlay_bet, refund_bet, settle_bets_for_match
from apps.exchange.services import place_order as place_exchange_order, cancel_order as cancel_exchange_order, settle_exchange_for_match, cash_out_order, preview_cash_out
from apps.exchange.models import ExchangeOrder, ExchangeFill
from apps.cashback.models import CashbackCredit
from apps.rewards.models import RewardPackage, UserRewardClaim
from apps.sports.models import Match, Sport, Tournament, RealtimeOddsConfig
from apps.wallet.models import Wallet, WalletTransaction
from apps.wallet.services import deposit_funds
from apps.payments.models import CryptoPayment
from apps.payments.providers import get_payment_provider
from apps.payments.services import (
    create_deposit,
    create_withdrawal_request,
    handle_deposit_success,
    initiate_manual_payout,
    confirm_manual_payout,
)
from apps.reports.services import get_overview_report, get_daily_report, get_sport_report, get_user_report, get_match_report

# Service functions in apps.bets / apps.wallet / apps.payments raise DRF's
# ValidationError, not Django's `ValidationError` (which this module uses for
# its own local, form-style validation). Any `except` clause that can catch
# an error coming out of a service call must include both.
SERVICE_ERRORS = (ValidationError, ServiceValidationError)


def _error_message(exc) -> str:
    """Extract a single human-readable message from either exception type."""
    if isinstance(exc, ServiceValidationError):
        detail = exc.detail
        if isinstance(detail, dict):
            for value in detail.values():
                return str(value[0]) if isinstance(value, list) and value else str(value)
        if isinstance(detail, list) and detail:
            return str(detail[0])
        return str(detail)
    if isinstance(exc, ValidationError):
        messages = getattr(exc, 'messages', None)
        if messages:
            return '; '.join(messages)
    return str(exc)


def _get_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _rate_limited(request, key_prefix: str, limit: int, window_seconds: int) -> bool:
    """
    Simple per-IP rate limiter for plain Django views (the web app has no
    DRF throttle classes to lean on). Returns True if the caller should be
    blocked. Used for registration and password-reset requests, which had
    no abuse protection at all before this — only the login view did.
    """
    key = f'{key_prefix}_{_get_ip(request)}'
    count = cache.get_or_set(key, 0, timeout=window_seconds)
    if count >= limit:
        return True
    cache.set(key, count + 1, timeout=window_seconds)
    return False


def _has_dashboard_access(user):
    if not user.is_authenticated:
        return False
    return user.is_staff or user.role in (User.Role.ADMIN, User.Role.MASTER)


def _is_full_admin(user):
    return bool(user.is_authenticated and (user.is_staff or user.is_superuser or user.role == User.Role.ADMIN))


def admin_dashboard_view(request):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    report = get_overview_report()
    recent_matches = Match.objects.select_related('sport', 'tournament').order_by('-start_time')[:10]

    return render(
        request,
        'web/dashboard.html',
        {
            'report': report,
            'recent_matches': recent_matches,
            'active': 'dashboard',
        },
    )


def admin_reports_view(request):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    report = get_overview_report()
    today = timezone.localdate()
    start_date = today - timedelta(days=6)
    daily = get_daily_report(start_date, today)
    sport_report = get_sport_report()
    user_report = get_user_report()
    match_report = get_match_report()

    return render(
        request,
        'web/admin_reports.html',
        {
            'report': report,
            'daily': daily,
            'sport_report': sport_report,
            'user_report': user_report,
            'match_report': match_report,
            'active': 'reports',
        },
    )


def admin_sports_view(request):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    sports = Sport.objects.prefetch_related('tournaments').order_by('name')
    error = None

    if request.method == 'POST':
        form_type = request.POST.get('form_type')
        try:
            if form_type == 'sport':
                name = request.POST.get('name', '').strip()
                slug = request.POST.get('slug', '').strip()
                if not name or not slug:
                    raise ValidationError('Sport name and slug are required.')
                # Sport.objects.create() bypasses the model's SlugField format
                # validation entirely (Django only validates on full_clean(),
                # not on save()) - check it explicitly so a bad slug can't
                # get in and break URL/provider-key matching downstream.
                validate_slug(slug)
                if Sport.objects.filter(slug=slug).exists():
                    raise ValidationError('Sport with this slug already exists.')
                Sport.objects.create(name=name, slug=slug)
            elif form_type == 'tournament':
                sport_id = request.POST.get('sport_id', '')
                name = request.POST.get('name', '').strip()
                season = request.POST.get('season', '').strip()
                try:
                    sport = Sport.objects.get(pk=sport_id)
                except Sport.DoesNotExist:
                    raise ValidationError('Selected sport does not exist.')
                if not name:
                    raise ValidationError('Tournament name is required.')
                Tournament.objects.create(sport=sport, name=name, season=season)
            else:
                raise ValidationError('Invalid form.')
        except ValidationError as exc:
            error = _error_message(exc)
        except IntegrityError:
            error = 'A tournament with this sport, name, and season already exists.'

        if not error:
            return redirect('web:admin_sports')
        else:
            sports = Sport.objects.prefetch_related('tournaments').order_by('name')

    return render(
        request,
        'web/admin_sports.html',
        {
            'sports': sports,
            'error': error,
            'active': 'admin_sports',
        },
    )


def admin_create_match_view(request):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    sports = Sport.objects.order_by('name')
    tournaments = Tournament.objects.select_related('sport').order_by('name')
    error = None

    if request.method == 'POST':
        try:
            sport_id = request.POST.get('sport_id', '').strip()
            tournament_id = request.POST.get('tournament_id', '').strip()
            home_team = request.POST.get('home_team', '').strip()
            away_team = request.POST.get('away_team', '').strip()
            start_time_raw = request.POST.get('start_time', '').strip()
            odds_home_raw = request.POST.get('odds_home', '').strip()
            odds_draw_raw = request.POST.get('odds_draw', '').strip()
            odds_away_raw = request.POST.get('odds_away', '').strip()

            if not home_team or not away_team:
                raise ValidationError('Team names are required.')
            if not start_time_raw:
                raise ValidationError('Start time is required.')

            start_time = parse_datetime(start_time_raw)
            if start_time is None:
                raise ValidationError('Invalid start time. Use YYYY-MM-DDTHH:MM format.')

            try:
                sport = Sport.objects.get(pk=sport_id)
            except Sport.DoesNotExist:
                raise ValidationError('Please select a valid sport.')

            tournament = None
            if tournament_id:
                tournament = Tournament.objects.filter(pk=tournament_id, sport=sport).first()
                if tournament is None:
                    raise ValidationError('Selected tournament is not valid for that sport.')

            def parse_decimal(value):
                if value == '' or value is None:
                    return None
                try:
                    d = Decimal(value)
                except InvalidOperation:
                    raise ValidationError('Odds must be a valid decimal number.')
                if d <= Decimal('1.00'):
                    raise ValidationError('Odds must be greater than 1.00.')
                return d

            Match.objects.create(
                sport=sport,
                tournament=tournament,
                home_team=home_team,
                away_team=away_team,
                start_time=start_time,
                status=Match.Status.SCHEDULED,
                odds_home=parse_decimal(odds_home_raw),
                odds_draw=parse_decimal(odds_draw_raw),
                odds_away=parse_decimal(odds_away_raw),
            )
            return redirect('web:admin_matches')
        except (ValidationError, InvalidOperation, ValueError) as exc:
            error = _error_message(exc)

    return render(
        request,
        'web/admin_match_create.html',
        {
            'sports': sports,
            'tournaments': tournaments,
            'error': error,
            'active': 'admin_matches',
        },
    )


def admin_matches_view(request):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    matches_list = Match.objects.select_related('sport', 'tournament').order_by('start_time')
    paginator = Paginator(matches_list, 20)
    page_number = request.GET.get('page')
    matches = paginator.get_page(page_number)

    return render(
        request,
        'web/admin_matches.html',
        {
            'matches': matches,
            'page_obj': matches,
            'active': 'admin_matches',
        },
    )


def admin_edit_match_view(request, match_id):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    match = get_object_or_404(Match.objects.select_related('sport', 'tournament'), pk=match_id)
    error = None

    if request.method == 'POST':
        try:
            home_team = request.POST.get('home_team', '').strip()
            away_team = request.POST.get('away_team', '').strip()
            status = request.POST.get('status', '').strip()
            start_time_raw = request.POST.get('start_time', '').strip()
            odds_home_raw = request.POST.get('odds_home', '').strip()
            odds_draw_raw = request.POST.get('odds_draw', '').strip()
            odds_away_raw = request.POST.get('odds_away', '').strip()
            odds_adjustment_raw = request.POST.get('odds_adjustment', '').strip()

            if not home_team or not away_team:
                raise ValidationError('Team names are required.')
            if status not in Match.Status.values:
                raise ValidationError('Invalid match status.')

            start_time = parse_datetime(start_time_raw)
            if start_time is None:
                raise ValidationError('Invalid start time. Use YYYY-MM-DDTHH:MM format.')

            def parse_decimal(value):
                if value == '' or value is None:
                    return None
                try:
                    d = Decimal(value)
                except InvalidOperation:
                    raise ValidationError('Odds must be a valid decimal number.')
                if d <= Decimal('1.00'):
                    raise ValidationError('Odds must be greater than 1.00.')
                return d

            def parse_adjustment(value):
                if value == '' or value is None:
                    return None
                try:
                    d = Decimal(value)
                except InvalidOperation:
                    raise ValidationError('Odds adjustment must be a valid decimal number.')
                if d < Decimal('-10.00') or d > Decimal('10.00'):
                    raise ValidationError('Odds adjustment must be between -10.00 and 10.00.')
                return d

            match.home_team = home_team
            match.away_team = away_team
            match.status = status
            match.start_time = start_time
            match.odds_home = parse_decimal(odds_home_raw)
            match.odds_draw = parse_decimal(odds_draw_raw)
            match.odds_away = parse_decimal(odds_away_raw)
            match.odds_adjustment = parse_adjustment(odds_adjustment_raw)

            match.save()
            return redirect('web:admin_matches')
        except (ValidationError, InvalidOperation, ValueError) as exc:
            error = _error_message(exc)

    return render(
        request,
        'web/admin_match_edit.html',
        {
            'match': match,
            'error': error,
            'active': 'admin_matches',
        },
    )


def admin_settle_match_view(request, match_id):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    if request.method != 'POST':
        return redirect('web:admin_matches')

    match = get_object_or_404(Match, pk=match_id)

    try:
        home_score = request.POST.get('home_score')
        away_score = request.POST.get('away_score')
        if home_score is not None and away_score is not None:
            match.home_score = int(home_score)
            match.away_score = int(away_score)
        match.status = Match.Status.FINISHED
        match.save()
        settle_bets_for_match(match)
        settle_exchange_for_match(match)
        create_audit_log(
            user=request.user,
            action='match_settled',
            target_type='match',
            target_id=match.id,
            metadata={'home_score': match.home_score, 'away_score': match.away_score},
            ip_address=_get_ip(request),
        )
    except (ValueError, ValidationError):
        pass

    return redirect('web:admin_matches')


def admin_cancel_match_view(request, match_id):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    if request.method != 'POST':
        return redirect('web:admin_matches')

    match = get_object_or_404(Match, pk=match_id)

    match.status = Match.Status.CANCELLED
    match.save()
    # This refunds pending single bets and marks parlay legs as refunded
    settle_bets_for_match(match)
    settle_exchange_for_match(match)
    create_audit_log(
        user=request.user,
        action='match_cancelled',
        target_type='match',
        target_id=match.id,
        ip_address=_get_ip(request),
    )

    return redirect('web:admin_matches')


def admin_withdrawals_view(request):
    """
    Lists withdrawal requests for an admin to process. Real crypto payouts
    always require a NOWPayments 2FA code per batch (their security design,
    not something this codebase can or should bypass) - see
    admin_withdrawal_process_view for that flow.
    """
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    pending = CryptoPayment.objects.filter(
        payment_type=CryptoPayment.PaymentType.WITHDRAWAL,
        status=CryptoPayment.Status.PENDING,
    ).select_related('user').order_by('-created_at')

    recent = CryptoPayment.objects.filter(
        payment_type=CryptoPayment.PaymentType.WITHDRAWAL,
    ).exclude(status=CryptoPayment.Status.PENDING).select_related('user').order_by('-updated_at')[:20]

    return render(
        request,
        'web/admin_withdrawals.html',
        {
            'pending': pending,
            'recent': recent,
            'nowpayments_active': get_payment_provider().name == 'nowpayments',
            'active': 'admin_withdrawals',
        },
    )


def admin_exchange_dashboard_view(request):
    """
    Read-only overview of exchange (back/lay) activity: summary stats,
    currently open orders, and matched fills. This is the custom-branded
    /panel/ equivalent of what Django's built-in /admin/ already exposes for
    ExchangeOrder/ExchangeFill (see apps/exchange/admin.py) - editing the
    commission rate still happens there; this page is purely for visibility
    inside the site's own admin experience. Adds no new wallet mutations.
    """
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    from django.db.models import Sum

    from apps.exchange.services import get_house_user

    house_user = get_house_user()

    since_7d = timezone.now() - timedelta(days=7)
    settled_fills = ExchangeFill.objects.filter(
        status__in=[ExchangeFill.Status.BACK_WON, ExchangeFill.Status.LAY_WON]
    )
    stats = {
        'commission_all_time': settled_fills.aggregate(s=Sum('commission_amount'))['s'] or Decimal('0'),
        'commission_7d': settled_fills.filter(created_at__gte=since_7d).aggregate(s=Sum('commission_amount'))['s'] or Decimal('0'),
        'open_orders_count': ExchangeOrder.objects.filter(status=ExchangeOrder.Status.OPEN).count(),
        'exposure_locked': Wallet.objects.aggregate(s=Sum('reserved_balance'))['s'] or Decimal('0'),
        'fills_settled_count': settled_fills.count(),
        'matched_volume_all_time': ExchangeFill.objects.aggregate(s=Sum('stake'))['s'] or Decimal('0'),
    }

    # --- Open Orders box ---
    orders_q = request.GET.get('orders_q', '').strip()
    orders_side = request.GET.get('orders_side', '').strip()
    orders_status = request.GET.get('orders_status', ExchangeOrder.Status.OPEN).strip()

    orders_qs = ExchangeOrder.objects.select_related('user', 'match').order_by('-created_at')
    if orders_q:
        orders_qs = orders_qs.filter(user__email__icontains=orders_q)
    if orders_side:
        orders_qs = orders_qs.filter(side=orders_side)
    if orders_status:
        orders_qs = orders_qs.filter(status=orders_status)

    if request.GET.get('export') == 'orders_xlsx':
        return _export_exchange_orders_xlsx(orders_qs)

    orders_paginator = Paginator(orders_qs, 20)
    orders_page = orders_paginator.get_page(request.GET.get('orders_page'))

    # --- Matched Fills box ---
    fills_status = request.GET.get('fills_status', '').strip()
    fills_selection = request.GET.get('fills_selection', '').strip()
    fills_date_from = request.GET.get('fills_date_from', '').strip()
    fills_date_to = request.GET.get('fills_date_to', '').strip()

    fills_qs = ExchangeFill.objects.select_related(
        'match', 'back_order__user', 'lay_order__user'
    ).order_by('-created_at')
    if fills_status:
        fills_qs = fills_qs.filter(status=fills_status)
    if fills_selection:
        fills_qs = fills_qs.filter(selection=fills_selection)
    fills_from_dt = _parse_pkt_date_bound(fills_date_from)
    fills_to_dt = _parse_pkt_date_bound(fills_date_to, end_of_day=True)
    if fills_from_dt:
        fills_qs = fills_qs.filter(created_at__gte=fills_from_dt)
    if fills_to_dt:
        fills_qs = fills_qs.filter(created_at__lte=fills_to_dt)

    if request.GET.get('export') == 'fills_xlsx':
        return _export_exchange_fills_xlsx(fills_qs)

    fills_paginator = Paginator(fills_qs, 20)
    fills_page = fills_paginator.get_page(request.GET.get('fills_page'))

    return render(
        request,
        'web/admin_exchange.html',
        {
            'stats': stats,
            'orders_page': orders_page,
            'orders_q': orders_q,
            'orders_side': orders_side,
            'orders_status': orders_status,
            'fills_page': fills_page,
            'fills_status': fills_status,
            'fills_selection': fills_selection,
            'fills_date_from': fills_date_from,
            'fills_date_to': fills_date_to,
            'order_side_choices': ExchangeOrder.Side.choices,
            'order_status_choices': ExchangeOrder.Status.choices,
            'fill_status_choices': ExchangeFill.Status.choices,
            'selection_choices': Bet.Selection.choices,
            'house_user_id': house_user.id if house_user else None,
            'active': 'admin_exchange',
        },
    )


def admin_cashback_dashboard_view(request):
    """
    Read-only overview of loss-cashback activity: summary stats and a
    searchable/filterable/paginated CashbackCredit table. Direct structural
    copy of admin_exchange_dashboard_view - same GET-param-prefix
    convention, same _parse_pkt_date_bound helper. Editing the global
    cashback rate/multiplier/on-off switch happens in Django's built-in
    /admin/ (see apps/cashback/admin.py); per-user overrides are edited on
    the existing admin_user_update_view page. Adds no new wallet mutations.
    """
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    from django.db.models import Sum

    since_7d = timezone.now() - timedelta(days=7)
    stats = {
        'credited_all_time': CashbackCredit.objects.aggregate(s=Sum('cashback_amount'))['s'] or Decimal('0'),
        'credited_7d': CashbackCredit.objects.filter(created_at__gte=since_7d).aggregate(s=Sum('cashback_amount'))['s'] or Decimal('0'),
        'locked_total': CashbackCredit.objects.filter(status=CashbackCredit.Status.LOCKED).aggregate(s=Sum('cashback_amount'))['s'] or Decimal('0'),
        'locked_count': CashbackCredit.objects.filter(status=CashbackCredit.Status.LOCKED).count(),
        'unlocked_count': CashbackCredit.objects.filter(status=CashbackCredit.Status.UNLOCKED).count(),
    }

    credits_q = request.GET.get('credits_q', '').strip()
    credits_status = request.GET.get('credits_status', '').strip()
    credits_date_from = request.GET.get('credits_date_from', '').strip()
    credits_date_to = request.GET.get('credits_date_to', '').strip()

    credits_qs = CashbackCredit.objects.select_related('user').order_by('-created_at')
    if credits_q:
        credits_qs = credits_qs.filter(user__email__icontains=credits_q)
    if credits_status:
        credits_qs = credits_qs.filter(status=credits_status)
    credits_from_dt = _parse_pkt_date_bound(credits_date_from)
    credits_to_dt = _parse_pkt_date_bound(credits_date_to, end_of_day=True)
    if credits_from_dt:
        credits_qs = credits_qs.filter(created_at__gte=credits_from_dt)
    if credits_to_dt:
        credits_qs = credits_qs.filter(created_at__lte=credits_to_dt)

    if request.GET.get('export') == 'credits_xlsx':
        return _export_cashback_credits_xlsx(credits_qs)

    credits_paginator = Paginator(credits_qs, 20)
    credits_page = credits_paginator.get_page(request.GET.get('credits_page'))

    return render(
        request,
        'web/admin_cashback.html',
        {
            'stats': stats,
            'credits_page': credits_page,
            'credits_q': credits_q,
            'credits_status': credits_status,
            'credits_date_from': credits_date_from,
            'credits_date_to': credits_date_to,
            'credit_status_choices': CashbackCredit.Status.choices,
            'active': 'admin_cashback',
        },
    )


def admin_rewards_view(request):
    """List of admin-managed deposit-milestone reward packages."""
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    from django.db.models import Count

    packages = RewardPackage.objects.annotate(claim_count=Count('claims')).order_by('deposit_threshold')

    return render(
        request,
        'web/admin_rewards.html',
        {
            'packages': packages,
            'active': 'admin_rewards',
        },
    )


def admin_create_reward_view(request):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    error = None
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            threshold_raw = request.POST.get('deposit_threshold', '').strip()
            bonus_raw = request.POST.get('commission_rate_bonus', '').strip()

            if not name:
                raise ValidationError('Name is required.')
            try:
                threshold = Decimal(threshold_raw)
            except InvalidOperation:
                raise ValidationError('Deposit threshold must be a valid decimal number.')
            if threshold <= 0:
                raise ValidationError('Deposit threshold must be greater than zero.')
            try:
                bonus = Decimal(bonus_raw)
            except InvalidOperation:
                raise ValidationError('Commission rate bonus must be a valid decimal number.')
            if not (0 <= bonus <= 99):
                raise ValidationError('Commission rate bonus must be between 0 and 99.')

            package = RewardPackage(name=name, deposit_threshold=threshold, commission_rate_bonus=bonus)
            package.full_clean()
            package.save()
            return redirect('web:admin_rewards')
        except (ValidationError, InvalidOperation) as exc:
            error = _error_message(exc)

    return render(
        request,
        'web/admin_reward_create.html',
        {'error': error, 'active': 'admin_rewards'},
    )


def admin_edit_reward_view(request, package_id):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    package = get_object_or_404(RewardPackage, pk=package_id)
    error = None

    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            threshold_raw = request.POST.get('deposit_threshold', '').strip()
            bonus_raw = request.POST.get('commission_rate_bonus', '').strip()
            is_active = request.POST.get('is_active') == 'on'

            if not name:
                raise ValidationError('Name is required.')
            try:
                threshold = Decimal(threshold_raw)
            except InvalidOperation:
                raise ValidationError('Deposit threshold must be a valid decimal number.')
            if threshold <= 0:
                raise ValidationError('Deposit threshold must be greater than zero.')
            try:
                bonus = Decimal(bonus_raw)
            except InvalidOperation:
                raise ValidationError('Commission rate bonus must be a valid decimal number.')
            if not (0 <= bonus <= 99):
                raise ValidationError('Commission rate bonus must be between 0 and 99.')

            package.name = name
            package.deposit_threshold = threshold
            package.commission_rate_bonus = bonus
            package.is_active = is_active
            package.full_clean()
            package.save()
            return redirect('web:admin_rewards')
        except (ValidationError, InvalidOperation) as exc:
            error = _error_message(exc)

    return render(
        request,
        'web/admin_reward_edit.html',
        {'package': package, 'error': error, 'active': 'admin_rewards'},
    )


def admin_withdrawal_process_view(request, payment_id):
    """
    Two-step manual payout flow for one withdrawal:
      step=login  -> admin enters their NOWPayments email+password directly
                     into this form (never sent to or stored by anyone but
                     NOWPayments itself); we exchange it for a short-lived
                     JWT and create the payout batch.
      step=verify -> NOWPayments requires a 2FA code to actually release the
                     payout; the JWT from step 1 is held only in this
                     admin's own server-side session, only until this step
                     completes or the session key is cleared.
    """
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    payment = get_object_or_404(
        CryptoPayment,
        pk=payment_id,
        payment_type=CryptoPayment.PaymentType.WITHDRAWAL,
    )

    session_key = f'payout_session_{payment_id}'
    session_data = request.session.get(session_key)
    error = None

    if request.method == 'POST':
        step = request.POST.get('step')

        if step == 'login':
            email = request.POST.get('email', '').strip()
            password = request.POST.get('password', '')
            if not email or not password:
                error = 'Email and password are required.'
            else:
                try:
                    result = initiate_manual_payout(
                        payment_id=payment.id, email=email, password=password,
                    )
                    request.session[session_key] = {
                        'batch_id': result['batch_id'],
                        'jwt_token': result['jwt_token'],
                    }
                    session_data = request.session[session_key]
                    create_audit_log(
                        user=request.user,
                        action='payout_batch_created',
                        target_type='crypto_payment',
                        target_id=payment.id,
                        metadata={'batch_id': result['batch_id']},
                        ip_address=_get_ip(request),
                    )
                except SERVICE_ERRORS as exc:
                    error = _error_message(exc)
            # `password` deliberately goes out of scope here and is never
            # referenced again - nothing beyond this request holds it.

        elif step == 'verify':
            code = request.POST.get('verification_code', '').strip()
            if not session_data:
                error = 'This session expired (NOWPayments codes/tokens are short-lived) - please log in again.'
            else:
                try:
                    confirm_manual_payout(
                        payment_id=payment.id,
                        batch_id=session_data['batch_id'],
                        verification_code=code,
                        jwt_token=session_data['jwt_token'],
                    )
                    del request.session[session_key]
                    create_audit_log(
                        user=request.user,
                        action='payout_verified',
                        target_type='crypto_payment',
                        target_id=payment.id,
                        ip_address=_get_ip(request),
                    )
                    return redirect('web:admin_withdrawals')
                except SERVICE_ERRORS as exc:
                    error = _error_message(exc)

    return render(
        request,
        'web/admin_withdrawal_process.html',
        {
            'payment': payment,
            'awaiting_2fa': bool(session_data),
            'error': error,
            'active': 'admin_withdrawals',
        },
    )


def admin_audit_logs_view(request):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    user_email = request.GET.get('user_email', '').strip()
    action = request.GET.get('action', '').strip()

    logs_list = AuditLog.objects.select_related('user').order_by('-created_at')
    if user_email:
        logs_list = logs_list.filter(user__email__icontains=user_email)
    if action:
        logs_list = logs_list.filter(action__icontains=action)

    if request.GET.get('export') == 'xlsx':
        return _export_audit_logs_xlsx(logs_list)

    paginator = Paginator(logs_list, 20)
    page_number = request.GET.get('page')
    logs = paginator.get_page(page_number)

    return render(
        request,
        'web/admin_audit.html',
        {
            'logs': logs,
            'page_obj': logs,
            'user_email': user_email,
            'action_filter': action,
            'active': 'audit',
        },
    )


def _currently_signed_in_user_ids():
    """
    "Online now" for a session-based Django app means: has a session that
    hasn't expired yet. There is no separate heartbeat/presence system, so
    this is the correct, honest definition (not an approximation) - a user
    is counted as signed in exactly as long as their login session is valid,
    which matches how being "logged in" actually works here.
    """
    from django.contrib.sessions.models import Session

    ids = set()
    for session in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        data = session.get_decoded()
        uid = data.get('_auth_user_id')
        if uid:
            ids.add(int(uid))
    return ids


def admin_users_view(request):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    from django.db.models import Count, OuterRef, Subquery, Sum
    from django.db.models.functions import Coalesce

    # Every per-user number below is a correlated subquery, not a joined
    # aggregate. Combining Count()/Sum() annotations over TWO different
    # reverse relations (bets AND crypto_payments) in one annotate() call
    # makes Django JOIN both tables together, which silently inflates every
    # aggregate via row fan-out (each bet row gets multiplied by each
    # payment row, and vice versa) - a real, easy-to-miss correctness bug.
    # Subqueries are each independently correlated per outer row, so this
    # can't happen. Verified against known test data before relying on this.
    def bet_count_subquery(status):
        return Coalesce(
            Subquery(
                Bet.objects.filter(user=OuterRef('pk'), status=status)
                .order_by().values('user').annotate(c=Count('id')).values('c')[:1]
            ),
            0,
        )

    def payment_sum_subquery(payment_type, since=None):
        qs = CryptoPayment.objects.filter(
            user=OuterRef('pk'),
            payment_type=payment_type,
            status=CryptoPayment.Status.COMPLETED,
        )
        if since is not None:
            qs = qs.filter(created_at__gte=since)
        return Coalesce(
            Subquery(
                qs.order_by().values('user').annotate(s=Sum('amount')).values('s')[:1]
            ),
            Decimal('0'),
        )

    first_bet_qs = Bet.objects.filter(user=OuterRef('pk')).order_by('created_at')
    last_bet_qs = Bet.objects.filter(user=OuterRef('pk')).order_by('-created_at')
    # "Today" means the Pakistan calendar day, not the UTC one - midnight UTC
    # and midnight PKT are 5 hours apart, so this must be computed in PKT
    # explicitly rather than truncating timezone.now() (which is UTC).
    today_start = timezone.now().astimezone(PAKISTAN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    users_list = User.objects.select_related('parent').annotate(
        first_bet_at=Subquery(first_bet_qs.values('created_at')[:1]),
        last_bet_at=Subquery(last_bet_qs.values('created_at')[:1]),
        first_bet_status=Subquery(first_bet_qs.values('status')[:1]),
        last_bet_status=Subquery(last_bet_qs.values('status')[:1]),
        won_count=bet_count_subquery(Bet.Status.WON),
        lost_count=bet_count_subquery(Bet.Status.LOST),
        pending_count=bet_count_subquery(Bet.Status.PENDING),
        deposit_today=payment_sum_subquery(CryptoPayment.PaymentType.DEPOSIT, since=today_start),
        withdraw_today=payment_sum_subquery(CryptoPayment.PaymentType.WITHDRAWAL, since=today_start),
        deposit_all_time=payment_sum_subquery(CryptoPayment.PaymentType.DEPOSIT),
        withdraw_all_time=payment_sum_subquery(CryptoPayment.PaymentType.WITHDRAWAL),
    )

    if not _is_full_admin(request.user):
        # A master only manages/sees their own direct downstream users.
        users_list = users_list.filter(parent_id=request.user.id)

    if request.GET.get('export') == 'users_master_xlsx':
        # Checked here deliberately - before any q/date_from/date_to/filter
        # narrowing below - so "master export" means "every user this admin
        # is allowed to manage," not literally every row regardless of who's
        # asking. Reusing the already-scoped users_list preserves the same
        # Master-vs-full-admin access boundary enforced above; bypassing it
        # here would reopen the class of privilege-escalation bug already
        # fixed once in this project.
        return _export_users_xlsx(users_list, 'all_users.xlsx')

    query = request.GET.get('q', '').strip()
    if query:
        users_list = users_list.filter(email__icontains=query)

    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    parsed_from = _parse_pkt_date_bound(date_from)
    parsed_to = _parse_pkt_date_bound(date_to, end_of_day=True)
    if parsed_from:
        users_list = users_list.filter(date_joined__gte=parsed_from)
    if parsed_to:
        users_list = users_list.filter(date_joined__lte=parsed_to)

    active_ids = None
    filter_by = request.GET.get('filter', '').strip()
    if filter_by == 'online':
        active_ids = _currently_signed_in_user_ids()
        users_list = users_list.filter(pk__in=active_ids)
    elif filter_by == 'has_pending_bet':
        users_list = users_list.filter(pending_count__gt=0)
    elif filter_by == 'first_bet_won':
        users_list = users_list.filter(first_bet_status=Bet.Status.WON)
    elif filter_by == 'first_bet_lost':
        users_list = users_list.filter(first_bet_status=Bet.Status.LOST)
    elif filter_by == 'last_bet_won':
        users_list = users_list.filter(last_bet_status=Bet.Status.WON)
    elif filter_by == 'last_bet_lost':
        users_list = users_list.filter(last_bet_status=Bet.Status.LOST)

    sort_by = request.GET.get('sort', 'joined').strip()
    sort_map = {
        'joined': '-date_joined',
        'first_bet': '-first_bet_at',
        'last_bet': '-last_bet_at',
        'most_wins': '-won_count',
        'most_losses': '-lost_count',
        'deposit_today': '-deposit_today',
        'withdraw_today': '-withdraw_today',
        'deposit_all_time': '-deposit_all_time',
        'withdraw_all_time': '-withdraw_all_time',
    }
    users_list = users_list.order_by(sort_map.get(sort_by, '-date_joined'), 'id')

    if request.GET.get('export') == 'users_xlsx':
        return _export_users_xlsx(users_list, 'users_filtered.xlsx')

    if active_ids is None and filter_by != 'online':
        # Only compute this once, and only when needed for display (the
        # small "online" dot next to each row), not when we already
        # filtered by it above.
        active_ids = _currently_signed_in_user_ids()

    paginator = Paginator(users_list, 20)
    page_number = request.GET.get('page')
    users = paginator.get_page(page_number)

    return render(
        request,
        'web/admin_users.html',
        {
            'users': users,
            'page_obj': users,
            'active_ids': active_ids,
            'query': query,
            'date_from': date_from,
            'date_to': date_to,
            'filter_by': filter_by,
            'sort_by': sort_by,
            'active': 'admin_users',
        },
    )


def admin_user_detail_view(request, user_id):
    """
    Read-only full history for one user: profile, wallet, every deposit/
    withdrawal, every bet and parlay bet. This is what an admin needs to
    actually investigate a user, not just edit their role/limits.

    Each of the five history boxes (wallet transactions, commission earned
    from downstream users, deposits/withdrawals, single bets, parlay bets)
    has its own independent search box, date range (Pakistan time, like
    every other admin filter), and pagination - prefixed
    wt_/ch_/cp_/sb_/pb_ respectively so they never collide with each
    other's query params on one page.
    """
    from django.db.models import Q

    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    target = get_object_or_404(User.objects.select_related('parent'), pk=user_id)

    if not user_can_manage_target(request.user, target):
        return render(
            request,
            'web/admin_user_detail.html',
            {'error': 'You cannot view this user.', 'view_user': target},
            status=403,
        )

    wallet, _ = Wallet.objects.get_or_create(user=target)

    def date_filtered(qs, field, prefix):
        q_from = request.GET.get(f'{prefix}_date_from', '').strip()
        q_to = request.GET.get(f'{prefix}_date_to', '').strip()
        dt_from = _parse_pkt_date_bound(q_from)
        dt_to = _parse_pkt_date_bound(q_to, end_of_day=True)
        if dt_from:
            qs = qs.filter(**{f'{field}__gte': dt_from})
        if dt_to:
            qs = qs.filter(**{f'{field}__lte': dt_to})
        return qs, q_from, q_to

    def paginated(qs, prefix, page_size=25):
        paginator = Paginator(qs, page_size)
        return paginator.get_page(request.GET.get(f'{prefix}_page'))

    # --- Wallet transactions ---
    wt_q = request.GET.get('wt_q', '').strip()
    transactions_qs = WalletTransaction.objects.filter(wallet=wallet).select_related('wallet').order_by('-created_at')
    if wt_q:
        transactions_qs = transactions_qs.filter(
            Q(txn_type__icontains=wt_q) | Q(description__icontains=wt_q) | Q(reference_id__icontains=wt_q)
        )
    transactions_qs, wt_date_from, wt_date_to = date_filtered(transactions_qs, 'created_at', 'wt')
    transactions_page = paginated(transactions_qs, 'wt')

    # --- Commission history (earned as this user's downstream children lose bets) ---
    from django.db.models import Sum

    commission_all_time = WalletTransaction.objects.filter(
        wallet=wallet, txn_type=WalletTransaction.TxnType.COMMISSION,
    ).aggregate(s=Sum('amount'))['s'] or Decimal('0')

    ch_q = request.GET.get('ch_q', '').strip()
    commissions_qs = WalletTransaction.objects.filter(
        wallet=wallet, txn_type=WalletTransaction.TxnType.COMMISSION,
    ).order_by('-created_at')
    if ch_q:
        commissions_qs = commissions_qs.filter(description__icontains=ch_q)
    commissions_qs, ch_date_from, ch_date_to = date_filtered(commissions_qs, 'created_at', 'ch')
    commissions_page = paginated(commissions_qs, 'ch')

    # --- Deposits / withdrawals ---
    cp_q = request.GET.get('cp_q', '').strip()
    payments_qs = CryptoPayment.objects.filter(user=target).order_by('-created_at')
    if cp_q:
        payments_qs = payments_qs.filter(
            Q(external_id__icontains=cp_q) | Q(address__icontains=cp_q) | Q(currency__icontains=cp_q)
        )
    payments_qs, cp_date_from, cp_date_to = date_filtered(payments_qs, 'created_at', 'cp')
    payments_page = paginated(payments_qs, 'cp')

    # --- Single bets ---
    sb_q = request.GET.get('sb_q', '').strip()
    bets_qs = Bet.objects.filter(user=target).select_related('match', 'match__sport').order_by('-created_at')
    if sb_q:
        bets_qs = bets_qs.filter(
            Q(match__home_team__icontains=sb_q) | Q(match__away_team__icontains=sb_q)
        )
    bets_qs, sb_date_from, sb_date_to = date_filtered(bets_qs, 'created_at', 'sb')
    bets_page = paginated(bets_qs, 'sb')

    # --- Parlay bets ---
    pb_q = request.GET.get('pb_q', '').strip()
    parlays_qs = ParlayBet.objects.filter(user=target).prefetch_related('legs__match').order_by('-created_at')
    if pb_q:
        parlays_qs = parlays_qs.filter(
            Q(legs__match__home_team__icontains=pb_q) | Q(legs__match__away_team__icontains=pb_q)
        ).distinct()
    parlays_qs, pb_date_from, pb_date_to = date_filtered(parlays_qs, 'created_at', 'pb')
    parlays_page = paginated(parlays_qs, 'pb')

    children = User.objects.filter(parent=target).order_by('email')

    return render(
        request,
        'web/admin_user_detail.html',
        {
            'view_user': target,
            'wallet': wallet,
            'transactions_page': transactions_page,
            'wt_q': wt_q, 'wt_date_from': wt_date_from, 'wt_date_to': wt_date_to,
            'commission_all_time': commission_all_time,
            'commissions_page': commissions_page,
            'ch_q': ch_q, 'ch_date_from': ch_date_from, 'ch_date_to': ch_date_to,
            'payments_page': payments_page,
            'cp_q': cp_q, 'cp_date_from': cp_date_from, 'cp_date_to': cp_date_to,
            'bets_page': bets_page,
            'sb_q': sb_q, 'sb_date_from': sb_date_from, 'sb_date_to': sb_date_to,
            'parlays_page': parlays_page,
            'pb_q': pb_q, 'pb_date_from': pb_date_from, 'pb_date_to': pb_date_to,
            'children': children,
            # Phone number is personal contact info - only the actual
            # superuser (the platform owner) sees it, not every "admin"
            # role staff account.
            'can_view_sensitive': _is_full_admin(request.user),
            'active': 'admin_users',
        },
    )


def _parse_pkt_date_bound(raw: str, end_of_day: bool = False):
    """
    Parses an admin-supplied date/time filter value and returns an
    aware datetime, explicitly localized to Pakistan time (PKT) - never the
    server's default timezone (UTC). All admin-facing date filters are
    Pakistan time per an explicit requirement, and this must be done
    explicitly: Django's ORM converts a naive datetime using
    settings.TIME_ZONE (UTC), NOT whatever timezone is currently
    `activate()`-d, even though the admin panel does activate PKT for
    *display*. Confirmed by direct testing - relying on activation alone for
    filtering silently produces UTC-shifted (wrong) results.

    Accepts either a plain date (`YYYY-MM-DD`, from a <input type="date">)
    or a full local datetime (`YYYY-MM-DDTHH:MM`, from
    <input type="datetime-local">). `end_of_day` only applies to the
    date-only form, to make an inclusive upper bound.
    """
    if not raw:
        return None
    if len(raw) == 10:  # YYYY-MM-DD, no time component supplied
        suffix = ' 23:59:59' if end_of_day else ' 00:00:00'
        raw = raw + suffix
    parsed = parse_datetime(raw)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, PAKISTAN_TZ)
    return parsed


def admin_user_ledger_view(request, user_id):
    """
    Full betting + deposit/withdrawal ledger for one user: two independent
    sections (bet history, deposit/withdrawal history), each with its own
    search box, date range, and adjustable page size, plus a CSV export of
    whatever is currently filtered (not limited to the on-screen page size -
    the on-screen table stays capped so the page can't be made to render
    10,000 rows and hang the browser; the CSV export has no such cap since
    it's a file download, not rendered HTML).
    """
    from django.db.models import Q

    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    target = get_object_or_404(User, pk=user_id)
    if not user_can_manage_target(request.user, target):
        return redirect('web:admin_users')

    # --- Bet history ---
    bet_q = request.GET.get('bet_q', '').strip()
    bet_date_from = request.GET.get('bet_date_from', '').strip()
    bet_date_to = request.GET.get('bet_date_to', '').strip()

    bets_qs = Bet.objects.filter(user=target).select_related('match', 'match__sport').order_by('-created_at')
    if bet_q:
        bets_qs = bets_qs.filter(
            Q(match__home_team__icontains=bet_q) | Q(match__away_team__icontains=bet_q)
        )
    bet_from_dt = _parse_pkt_date_bound(bet_date_from)
    bet_to_dt = _parse_pkt_date_bound(bet_date_to, end_of_day=True)
    if bet_from_dt:
        bets_qs = bets_qs.filter(created_at__gte=bet_from_dt)
    if bet_to_dt:
        bets_qs = bets_qs.filter(created_at__lte=bet_to_dt)

    if request.GET.get('export') == 'bets_csv':
        return _export_bets_csv(target, bets_qs)
    if request.GET.get('export') == 'bets_xlsx':
        return _export_bets_xlsx(target, bets_qs)

    bet_page_size = _clamp_page_size(request.GET.get('bet_page_size'), default=50)
    bet_paginator = Paginator(bets_qs, bet_page_size)
    bets_page = bet_paginator.get_page(request.GET.get('bet_page'))

    # --- Deposit / withdrawal history ---
    pay_q = request.GET.get('pay_q', '').strip()
    pay_date_from = request.GET.get('pay_date_from', '').strip()
    pay_date_to = request.GET.get('pay_date_to', '').strip()

    payments_qs = CryptoPayment.objects.filter(user=target).order_by('-created_at')
    if pay_q:
        payments_qs = payments_qs.filter(
            Q(external_id__icontains=pay_q) | Q(address__icontains=pay_q) | Q(currency__icontains=pay_q)
        )
    pay_from_dt = _parse_pkt_date_bound(pay_date_from)
    pay_to_dt = _parse_pkt_date_bound(pay_date_to, end_of_day=True)
    if pay_from_dt:
        payments_qs = payments_qs.filter(created_at__gte=pay_from_dt)
    if pay_to_dt:
        payments_qs = payments_qs.filter(created_at__lte=pay_to_dt)

    if request.GET.get('export') == 'payments_csv':
        return _export_payments_csv(target, payments_qs)
    if request.GET.get('export') == 'payments_xlsx':
        return _export_payments_xlsx(target, payments_qs)

    pay_page_size = _clamp_page_size(request.GET.get('pay_page_size'), default=50)
    pay_paginator = Paginator(payments_qs, pay_page_size)
    payments_page = pay_paginator.get_page(request.GET.get('pay_page'))

    return render(
        request,
        'web/admin_user_ledger.html',
        {
            'view_user': target,
            'bets_page': bets_page,
            'bet_q': bet_q,
            'bet_date_from': bet_date_from,
            'bet_date_to': bet_date_to,
            'bet_page_size': bet_page_size,
            'payments_page': payments_page,
            'pay_q': pay_q,
            'pay_date_from': pay_date_from,
            'pay_date_to': pay_date_to,
            'pay_page_size': pay_page_size,
            'active': 'admin_users',
        },
    )


def _clamp_page_size(raw, default=50, minimum=10, maximum=10000):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _export_bets_csv(target, bets_qs):
    import csv
    from django.http import HttpResponse

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{target.email}_bets.csv"'
    writer = csv.writer(response)
    writer.writerow(['Date', 'Match', 'Selection', 'Odds', 'Stake', 'Potential Payout', 'Status'])
    for bet in bets_qs.iterator():
        writer.writerow([
            bet.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            f'{bet.match.home_team} v {bet.match.away_team}',
            bet.selection,
            bet.odds,
            bet.stake,
            bet.potential_payout,
            bet.status,
        ])
    return response


def _export_payments_csv(target, payments_qs):
    import csv
    from django.http import HttpResponse

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{target.email}_payments.csv"'
    writer = csv.writer(response)
    writer.writerow(['Date', 'Type', 'Amount', 'Currency', 'Status', 'Provider', 'Address', 'External ID'])
    for p in payments_qs.iterator():
        writer.writerow([
            p.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            p.payment_type,
            p.amount,
            p.currency,
            p.status,
            p.provider,
            p.address,
            p.external_id,
        ])
    return response


def _build_xlsx_response(filename, headers, rows):
    """
    Shared .xlsx writer used by every _export_*_xlsx function below - one
    Workbook, a bold header row, one row per item. Money fields must be
    passed in already as `str(decimal_value)` (never float) to avoid
    floating-point misrepresentation, and datetimes as pre-formatted
    strings (openpyxl rejects timezone-aware datetime cells outright) -
    both conventions match what the existing CSV exports already do.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from django.http import HttpResponse

    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


def _export_bets_xlsx(target, bets_qs):
    headers = ['Date', 'Match', 'Selection', 'Odds', 'Stake', 'Potential Payout', 'Status']
    rows = (
        [
            bet.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            f'{bet.match.home_team} v {bet.match.away_team}',
            bet.selection,
            str(bet.odds),
            str(bet.stake),
            str(bet.potential_payout),
            bet.status,
        ]
        for bet in bets_qs.iterator()
    )
    return _build_xlsx_response(f'{target.email}_bets.xlsx', headers, rows)


def _export_payments_xlsx(target, payments_qs):
    headers = ['Date', 'Type', 'Amount', 'Currency', 'Status', 'Provider', 'Address', 'External ID']
    rows = (
        [
            p.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            p.payment_type,
            str(p.amount),
            p.currency,
            p.status,
            p.provider,
            p.address,
            p.external_id,
        ]
        for p in payments_qs.iterator()
    )
    return _build_xlsx_response(f'{target.email}_payments.xlsx', headers, rows)


def _export_users_xlsx(users_qs, filename):
    """
    Shared by both the Manage Users "current filter" export and the master
    "all users" export - same columns either way, only the queryset and
    filename passed in differ. Never includes `password` (the hash) or any
    token field. Relies on the same annotations admin_users_view already
    puts on `users_qs` (won_count, deposit_today, etc.).
    """
    headers = [
        'Email', 'Phone', 'Role', 'Parent', 'Commission Rate', 'Min Bet', 'Max Bet',
        'Betting Enabled', 'Cashback Enabled Override', 'Cashback Rate Override',
        'Cashback Wagering Multiplier Override', 'Date Joined', 'Active', 'Staff',
        'Last Login', 'Won', 'Lost', 'Pending', 'Deposited Today', 'Withdrawn Today',
        'Deposited All-Time', 'Withdrawn All-Time',
    ]

    def _fmt_dt(value):
        return value.strftime('%Y-%m-%d %H:%M:%S') if value else ''

    def _fmt_decimal(value):
        return str(value) if value is not None else ''

    rows = (
        [
            u.email,
            u.phone_number,
            u.role,
            u.parent.email if u.parent_id else '',
            _fmt_decimal(u.commission_rate),
            _fmt_decimal(u.min_bet_amount),
            _fmt_decimal(u.max_bet_amount),
            u.is_betting_enabled,
            u.cashback_enabled_override if u.cashback_enabled_override is not None else '',
            _fmt_decimal(u.cashback_rate_override),
            _fmt_decimal(u.cashback_wagering_multiplier_override),
            _fmt_dt(u.date_joined),
            u.is_active,
            u.is_staff,
            _fmt_dt(u.last_login),
            u.won_count,
            u.lost_count,
            u.pending_count,
            _fmt_decimal(u.deposit_today),
            _fmt_decimal(u.withdraw_today),
            _fmt_decimal(u.deposit_all_time),
            _fmt_decimal(u.withdraw_all_time),
        ]
        for u in users_qs.iterator()
    )
    return _build_xlsx_response(filename, headers, rows)


def _export_exchange_orders_xlsx(orders_qs):
    headers = ['User', 'Match', 'Selection', 'Side', 'Odds', 'Stake', 'Matched', 'Unmatched', 'Status', 'Date']
    rows = (
        [
            order.user.email,
            f'{order.match.home_team} v {order.match.away_team}',
            order.selection,
            order.side,
            str(order.odds),
            str(order.stake),
            str(order.matched_stake),
            str(order.unmatched_stake),
            order.status,
            order.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        ]
        for order in orders_qs.iterator()
    )
    return _build_xlsx_response('exchange_orders.xlsx', headers, rows)


def _export_exchange_fills_xlsx(fills_qs):
    headers = ['Match', 'Selection', 'Back User', 'Lay User', 'Odds', 'Stake', 'Status', 'Commission', 'Date']
    rows = (
        [
            f'{fill.match.home_team} v {fill.match.away_team}',
            fill.selection,
            fill.back_order.user.email,
            fill.lay_order.user.email,
            str(fill.odds),
            str(fill.stake),
            fill.status,
            str(fill.commission_amount) if fill.commission_amount is not None else '',
            fill.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        ]
        for fill in fills_qs.iterator()
    )
    return _build_xlsx_response('exchange_fills.xlsx', headers, rows)


def _export_cashback_credits_xlsx(credits_qs):
    headers = ['User', 'Source', 'Loss', 'Rate', 'Cashback', 'Multiplier', 'Wagered', 'Required', 'Status', 'Date']
    rows = (
        [
            credit.user.email,
            credit.source_description,
            str(credit.loss_amount),
            str(credit.rate_applied),
            str(credit.cashback_amount),
            str(credit.multiplier_applied),
            str(credit.wagering_progress),
            str(credit.wagering_required),
            credit.status,
            credit.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        ]
        for credit in credits_qs.iterator()
    )
    return _build_xlsx_response('cashback_credits.xlsx', headers, rows)


def _export_audit_logs_xlsx(logs_qs):
    headers = ['User', 'Action', 'Target Type', 'Target ID', 'Metadata', 'IP', 'Date']
    rows = (
        [
            log.user.email if log.user_id else 'System',
            log.action,
            log.target_type or '',
            log.target_id or '',
            str(log.metadata) if log.metadata else '',
            log.ip_address or '',
            log.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        ]
        for log in logs_qs.iterator()
    )
    return _build_xlsx_response('audit_logs.xlsx', headers, rows)


def admin_bet_detail_view(request, bet_id):
    """One bet's full detail, plus how many times this user has bet on this match."""
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    bet = get_object_or_404(Bet.objects.select_related('match', 'match__sport', 'user'), pk=bet_id)
    if not user_can_manage_target(request.user, bet.user):
        return redirect('web:admin_users')

    times_bet_on_match = Bet.objects.filter(user=bet.user, match=bet.match).count()

    return render(
        request,
        'web/admin_bet_detail.html',
        {
            'bet': bet,
            'times_bet_on_match': times_bet_on_match,
            'active': 'admin_users',
        },
    )


def admin_reset_user_password_view(request, user_id):
    """
    Superuser-only: generate a brand-new random password for a user and show
    it once on screen so it can be relayed (e.g. via WhatsApp) to a user who
    can't do a self-service reset. The user's OLD password is never seen or
    recoverable by anyone - this creates a new one instead, which is the
    safe way to solve "user forgot their password and can't do email reset".
    """
    if not _is_full_admin(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    if request.method != 'POST':
        return redirect('web:admin_user_detail', user_id=user_id)

    target = get_object_or_404(User, pk=user_id)
    if not user_can_manage_target(request.user, target):
        return redirect('web:admin_users')

    import secrets
    new_password = secrets.token_urlsafe(9)  # readable-ish, ~12 chars
    target.set_password(new_password)
    target.save(update_fields=['password'])

    create_audit_log(
        user=request.user,
        action='password_reset_by_admin',
        target_type='user',
        target_id=target.id,
        ip_address=_get_ip(request),
    )

    return render(
        request,
        'web/admin_password_reset_result.html',
        {'target_user': target, 'new_password': new_password},
    )


def admin_user_update_view(request, user_id):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    user = get_object_or_404(User, pk=user_id)

    if not user_can_manage_target(request.user, user):
        return render(
            request,
            'web/admin_user_edit.html',
            {'error': 'You cannot manage this user.', 'edit_user': user, 'possible_parents': User.objects.none()},
            status=403,
        )

    is_full_admin = _is_full_admin(request.user)
    allowed_roles = roles_assignable_by(request.user)
    possible_parents = User.objects.exclude(pk=user.pk).order_by('email') if is_full_admin else User.objects.none()

    if request.method == 'POST':
        try:
            new_role = request.POST.get('role')
            if new_role not in allowed_roles:
                raise ValidationError('You are not allowed to assign that role.')

            parent_raw = request.POST.get('parent_id', '').strip()
            min_bet_raw = request.POST.get('min_bet_amount', '').strip()
            max_bet_raw = request.POST.get('max_bet_amount', '').strip()
            commission_raw = request.POST.get('commission_rate', '').strip()
            is_betting_enabled = request.POST.get('is_betting_enabled') == 'on'
            cashback_enabled_raw = request.POST.get('cashback_enabled_override', '').strip()
            cashback_rate_raw = request.POST.get('cashback_rate_override', '').strip()
            cashback_multiplier_raw = request.POST.get('cashback_wagering_multiplier_override', '').strip()

            user.role = new_role
            if _is_full_admin(request.user):
                # Phone number is personal contact info - only the actual
                # superuser (platform owner) may view/edit it.
                user.phone_number = request.POST.get('phone_number', '').strip()

            if is_full_admin:
                # Only a full admin may reassign which master/agent a user reports to.
                if parent_raw:
                    try:
                        user.parent_id = int(parent_raw)
                    except (TypeError, ValueError):
                        raise ValidationError('Invalid parent user.')
                else:
                    user.parent = None
            # Masters editing their own direct child leave `parent` untouched.

            # Parse optional bet limits
            user.min_bet_amount = Decimal(min_bet_raw) if min_bet_raw else None
            user.max_bet_amount = Decimal(max_bet_raw) if max_bet_raw else None

            # Parse optional commission rate. Empty means zero.
            try:
                new_commission = Decimal(commission_raw) if commission_raw else Decimal('0')
            except InvalidOperation:
                raise ValidationError('Commission rate must be a valid decimal number.')
            if new_commission < 0:
                raise ValidationError('Commission rate cannot be negative.')
            if new_commission > 100:
                raise ValidationError('Commission rate cannot exceed 100%.')
            user.commission_rate = new_commission

            if user.min_bet_amount is not None and user.min_bet_amount < 0:
                raise ValidationError('Minimum bet cannot be negative.')
            if user.max_bet_amount is not None and user.max_bet_amount < 0:
                raise ValidationError('Maximum bet cannot be negative.')

            user.is_betting_enabled = is_betting_enabled

            # Three-way: blank = inherit the global cashback setting.
            if cashback_enabled_raw == 'on':
                user.cashback_enabled_override = True
            elif cashback_enabled_raw == 'off':
                user.cashback_enabled_override = False
            else:
                user.cashback_enabled_override = None

            try:
                user.cashback_rate_override = Decimal(cashback_rate_raw) if cashback_rate_raw else None
            except InvalidOperation:
                raise ValidationError('Cashback rate override must be a valid decimal number.')
            if user.cashback_rate_override is not None and not (0 <= user.cashback_rate_override <= 99):
                raise ValidationError('Cashback rate override must be between 0 and 99.')

            try:
                user.cashback_wagering_multiplier_override = (
                    Decimal(cashback_multiplier_raw) if cashback_multiplier_raw else None
                )
            except InvalidOperation:
                raise ValidationError('Cashback wagering multiplier override must be a valid decimal number.')
            if user.cashback_wagering_multiplier_override is not None and not (
                0 <= user.cashback_wagering_multiplier_override <= 20
            ):
                raise ValidationError('Cashback wagering multiplier override must be between 0 and 20.')

            user.save()

            create_audit_log(
                user=request.user,
                action='user_updated',
                target_type='user',
                target_id=user.id,
                metadata={
                    'role': user.role,
                    'commission_rate': str(user.commission_rate),
                    'is_betting_enabled': user.is_betting_enabled,
                    'cashback_enabled_override': user.cashback_enabled_override,
                },
                ip_address=_get_ip(request),
            )

            return redirect('web:admin_users')
        except (ValidationError, InvalidOperation, ValueError) as exc:
            return render(
                request,
                'web/admin_user_edit.html',
                {
                    'error': _error_message(exc),
                    'edit_user': user,
                    'possible_parents': possible_parents,
                    'allowed_roles': allowed_roles,
                    'can_edit_parent': is_full_admin,
                    'can_view_sensitive': _is_full_admin(request.user),
                },
            )

    return render(
        request,
        'web/admin_user_edit.html',
        {
            'edit_user': user,
            'possible_parents': possible_parents,
            'allowed_roles': allowed_roles,
            'can_edit_parent': is_full_admin,
            'can_view_sensitive': _is_full_admin(request.user),
        },
    )


def home_view(request):
    matches = Match.objects.select_related('sport', 'tournament').filter(
        status__in=[Match.Status.SCHEDULED, Match.Status.LIVE],
    )[:20]
    return render(request, 'web/home.html', {'matches': matches, 'active': 'home'})


def register_view(request):
    if request.method == 'POST':
        if _rate_limited(request, 'register_attempts', limit=10, window_seconds=3600):
            return render(
                request,
                'web/register.html',
                {'error': 'Too many registration attempts from this address. Try again later.'},
            )

        email = request.POST.get('email', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()
        password = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')

        if not email:
            return render(
                request,
                'web/register.html',
                {'error': 'Email is required.'},
            )
        if password != password2:
            return render(
                request,
                'web/register.html',
                {'error': 'Passwords do not match.'},
            )
        if User.objects.filter(email=email).exists():
            return render(
                request,
                'web/register.html',
                {'error': 'User with this email already exists.'},
            )

        user = User.objects.create_user(email=email, password=password, phone_number=phone_number)
        auth_login(request, user)
        return redirect('web:home')

    return render(request, 'web/register.html')


def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')

        lockout_key = f'login_lockout_{email.lower()}'
        attempts_key = f'login_attempts_{email.lower()}'

        if cache.get(lockout_key):
            return render(
                request,
                'web/login.html',
                {'error': 'Too many failed attempts. Try again in 1 minute.'},
            )

        user = authenticate(request, email=email, password=password)
        if user is not None:
            cache.delete(attempts_key)
            cache.delete(lockout_key)
            auth_login(request, user)
            return redirect('web:home')

        failed = cache.get_or_set(attempts_key, 0, timeout=60)
        failed += 1
        cache.set(attempts_key, failed, timeout=60)
        if failed >= 5:
            cache.set(lockout_key, True, timeout=60)

        return render(
            request,
            'web/login.html',
            {'error': 'Invalid email or password.'},
        )

    return render(request, 'web/login.html')


@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect('web:home')

def password_reset_request_view(request):
    if request.method == 'POST':
        if _rate_limited(request, 'password_reset_attempts', limit=5, window_seconds=3600):
            return render(
                request,
                'web/reset_password.html',
                {'error': 'Too many password reset requests from this address. Try again later.'},
            )

        email = request.POST.get('email', '').strip()
        try:
            user = User.objects.get(email=email)
            PasswordResetToken.objects.filter(user=user, is_used=False).update(is_used=True)

            import secrets
            token = secrets.token_urlsafe(40)
            PasswordResetToken.objects.create(user=user, token=token)

            # Send email (in development with EMAIL_BACKEND=console it will print)
            send_mail(
                subject='QuickPayBet Password Reset',
                message=(
                    f'Hello {user.email},\n\n'
                    f'You requested a password reset.\n'
                    f'Your reset token is:\n\n{token}\n\n'
                    'Use it with the Password Reset Confirm page.'
                ),
                from_email=None,
                recipient_list=[user.email],
                fail_silently=False,
            )

            # Show token only in DEBUG so local developers can still test the flow
            show_token = settings.DEBUG
        except User.DoesNotExist:
            show_token = False

        return render(
            request,
            'web/reset_password_done.html',
            {'token': token if show_token else None},
        )

    return render(request, 'web/reset_password.html')

def password_reset_confirm_view(request):
    if request.method == 'POST':
        token = request.POST.get('token', '').strip()
        new_password = request.POST.get('password1', '')
        confirm_password = request.POST.get('password2', '')

        error = None

        if not token:
            error = 'Token is required.'
        elif new_password != confirm_password:
            error = 'Passwords do not match.'
        else:
            try:
                reset_obj = PasswordResetToken.objects.get(token=token, is_used=False)
            except PasswordResetToken.DoesNotExist:
                reset_obj = None
                error = 'Invalid or expired reset token.'

            if reset_obj is not None:
                user = reset_obj.user
                user.set_password(new_password)
                user.save()
                reset_obj.is_used = True
                reset_obj.save()
                return redirect('web:login')

        return render(
            request,
            'web/reset_password_confirm.html',
            {'error': error},
        )

    return render(request, 'web/reset_password_confirm.html')


def matches_view(request):
    """
    Three views for a regular visitor:
      - upcoming (default): scheduled/live matches, soonest first
      - results: finished matches from the last 7 days, most recent first
      - all: full match history, every sport, most recent first
    Optionally narrowed to one sport (football, cricket, whatever is active).
    """
    view = request.GET.get('view', 'upcoming').strip()
    if view not in ('upcoming', 'results', 'all'):
        view = 'upcoming'
    sport_slug = request.GET.get('sport', '').strip()

    matches_list = Match.objects.select_related('sport', 'tournament')
    if sport_slug:
        matches_list = matches_list.filter(sport__slug=sport_slug)

    if view == 'results':
        seven_days_ago = timezone.now() - timedelta(days=7)
        matches_list = matches_list.filter(
            status=Match.Status.FINISHED,
            start_time__gte=seven_days_ago,
        ).order_by('-start_time')
    elif view == 'all':
        matches_list = matches_list.order_by('-start_time')
    else:
        matches_list = matches_list.filter(
            status__in=[Match.Status.SCHEDULED, Match.Status.LIVE],
        ).order_by('start_time')

    paginator = Paginator(matches_list, 20)
    page_number = request.GET.get('page')
    matches = paginator.get_page(page_number)

    sports = Sport.objects.filter(is_active=True).order_by('name')

    return render(
        request,
        'web/matches.html',
        {
            'matches': matches,
            'page_obj': matches,
            'sports': sports,
            'selected_sport': sport_slug,
            'view': view,
            'active': 'matches',
        },
    )


def match_detail_view(request, pk):
    match = get_object_or_404(Match.objects.select_related('sport', 'tournament'), pk=pk)
    place_error = None

    if request.method == 'POST' and request.user.is_authenticated:
        try:
            selection = request.POST.get('selection')
            stake_raw = request.POST.get('stake')
            odds_shown_raw = request.POST.get('odds_shown', '').strip()
            if not selection or not stake_raw:
                raise ValidationError('Please provide selection and stake.')

            stake = Decimal(stake_raw)
            odds_shown = Decimal(odds_shown_raw) if odds_shown_raw else None
            bet = place_bet(
                user=request.user,
                match_id=match.id,
                selection=selection,
                stake=stake,
                odds_shown=odds_shown,
            )
            return redirect('web:match_detail', pk=match.pk)
        except SERVICE_ERRORS as exc:
            place_error = _error_message(exc)
        except InvalidOperation:
            place_error = 'Invalid stake.'
    elif request.method == 'POST' and not request.user.is_authenticated:
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    from apps.exchange.services import get_order_book

    my_exchange_orders = []
    if request.user.is_authenticated:
        my_exchange_orders = list(
            ExchangeOrder.objects.filter(match=match, user=request.user, status=ExchangeOrder.Status.OPEN)
            .order_by('-created_at')
        )

    realtime_config = RealtimeOddsConfig.get_solo()

    return render(
        request,
        'web/match_detail.html',
        {
            'match': match,
            'error': place_error,
            'order_book': get_order_book(match) if match.status in (Match.Status.SCHEDULED, Match.Status.LIVE) else None,
            'my_exchange_orders': my_exchange_orders,
            'exchange_error': request.GET.get('exchange_error', ''),
            'exchange_message': request.GET.get('exchange_message', ''),
            'realtime_enabled': realtime_config.is_enabled,
            'active': 'matches',
        },
    )


def match_odds_poll_view(request, pk):
    """
    Read-only JSON endpoint for live odds polling. No auth required (odds
    are public) - viewer identity for the "how many people are watching
    this match" gate is the visitor's own session, created here if one
    doesn't exist yet, which works uniformly for logged-in and anonymous
    visitors.

    Nothing here calls the real odds provider directly on every request -
    apps.sports.realtime.maybe_refresh_sport_odds() only actually does that
    at most once per configured interval per sport, regardless of how many
    viewers are polling concurrently.
    """
    from apps.sports.realtime import (
        get_effective_refresh_interval,
        maybe_refresh_sport_odds,
        record_viewer_heartbeat,
    )

    match = get_object_or_404(Match, pk=pk)
    config = RealtimeOddsConfig.get_solo()

    interval = get_effective_refresh_interval(match, config) if config.is_enabled else None

    if interval is not None:
        if not request.session.session_key:
            request.session.save()
        viewer_count = record_viewer_heartbeat(match, request.session.session_key, stale_after_seconds=interval * 3)
        if viewer_count >= config.min_viewers_for_realtime:
            maybe_refresh_sport_odds(match.sport, interval)
            match.refresh_from_db(fields=['odds_home', 'odds_draw', 'odds_away'])

    return JsonResponse({
        'odds_home': str(match.odds_home) if match.odds_home is not None else None,
        'odds_draw': str(match.odds_draw) if match.odds_draw is not None else None,
        'odds_away': str(match.odds_away) if match.odds_away is not None else None,
        'status': match.status,
        'should_continue_polling': interval is not None,
        'next_poll_seconds': interval or 60,
    })

@login_required
def place_bet_view(request, pk):
    match = get_object_or_404(Match, pk=pk)
    if request.method == 'POST':
        try:
            selection = request.POST.get('selection')
            stake_raw = request.POST.get('stake')
            stake = Decimal(stake_raw)
            place_bet(
                user=request.user,
                match_id=match.id,
                selection=selection,
                stake=stake,
            )
        except SERVICE_ERRORS as exc:
            return render(
                request,
                'web/match_detail.html',
                {'match': match, 'error': _error_message(exc)},
            )
        except InvalidOperation:
            return render(
                request,
                'web/match_detail.html',
                {'match': match, 'error': 'Invalid stake.'},
            )
    return redirect('web:match_detail', pk=match.pk)


def _redirect_with_exchange_error(match_pk, message):
    from urllib.parse import urlencode
    url = reverse('web:match_detail', kwargs={'pk': match_pk})
    return redirect(f'{url}?{urlencode({"exchange_error": message})}')


def _redirect_with_exchange_message(match_pk, message):
    """Same as _redirect_with_exchange_error, for non-error informational results (e.g. cash-out outcomes)."""
    from urllib.parse import urlencode
    url = reverse('web:match_detail', kwargs={'pk': match_pk})
    return redirect(f'{url}?{urlencode({"exchange_message": message})}')


@login_required
@require_POST
def exchange_place_order_view(request, pk):
    match = get_object_or_404(Match, pk=pk)
    try:
        selection = request.POST.get('selection')
        side = request.POST.get('side')
        odds = Decimal(request.POST.get('odds', ''))
        stake = Decimal(request.POST.get('stake', ''))
        place_exchange_order(
            user=request.user,
            match_id=match.id,
            selection=selection,
            side=side,
            odds=odds,
            stake=stake,
        )
    except SERVICE_ERRORS as exc:
        return _redirect_with_exchange_error(match.pk, _error_message(exc))
    except InvalidOperation:
        return _redirect_with_exchange_error(match.pk, 'Invalid odds or stake.')
    return redirect('web:match_detail', pk=match.pk)


@login_required
@require_POST
def exchange_cancel_order_view(request, order_id):
    order = get_object_or_404(ExchangeOrder, pk=order_id, user=request.user)
    try:
        cancel_exchange_order(user=request.user, order_id=order.id)
    except SERVICE_ERRORS:
        pass
    return redirect('web:match_detail', pk=order.match_id)


@login_required
def exchange_cash_out_preview_view(request, order_id):
    """
    Read-only JSON estimate for the live-updating Cash Out button label.
    Never trusted for execution - exchange_cash_out_view always recomputes
    fresh against the real book at the moment of the click.
    """
    order = get_object_or_404(ExchangeOrder, pk=order_id, user=request.user)
    result = preview_cash_out(order)
    estimated_value = result.get('estimated_value')
    return JsonResponse({
        'status': result['status'],
        'estimated_value': f'{estimated_value:.2f}' if estimated_value is not None else None,
    })


@login_required
@require_POST
def exchange_cash_out_view(request, order_id):
    order = get_object_or_404(ExchangeOrder, pk=order_id, user=request.user)
    try:
        result = cash_out_order(user=request.user, order_id=order.id)
    except SERVICE_ERRORS as exc:
        return _redirect_with_exchange_error(order.match_id, _error_message(exc))

    if result['status'] == 'full':
        # "Estimated", not "guaranteed": commission is charged per-bet, not
        # on a user's net result for the match, so the exact amount can
        # differ slightly (by a few percent of the commission rate) depending
        # on which way the match actually goes - see Section 11 rule 20.
        message = f"Cashed out for an estimated {result['estimated_value']:.2f}."
    elif result['status'] == 'partial':
        message = (
            f"Partially cashed out {result['matched_amount']:.2f} of your {order.matched_stake:.2f} stake "
            f"- the rest of your bet is still live and depends on the match result. "
            f"You can try cashing out the remainder again later."
        )
    else:
        message = "No cash-out liquidity available right now. Try again shortly."

    return _redirect_with_exchange_message(order.match_id, message)


@login_required
def parlay_bet_view(request):
    matches = Match.objects.filter(
        status__in=[Match.Status.SCHEDULED, Match.Status.LIVE],
        odds_home__isnull=False,
        odds_away__isnull=False,
    ).select_related('sport', 'tournament').order_by('start_time')

    error = None

    if request.method == 'POST':
        try:
            stake_raw = request.POST.get('stake')
            if not stake_raw:
                raise ValidationError('Stake is required.')
            stake = Decimal(stake_raw)
            if stake <= 0:
                raise ValidationError('Stake must be positive.')

            selections = []
            for key, value in request.POST.items():
                if key.startswith('selection_'):
                    try:
                        match_id = int(key.split('_')[1])
                    except (ValueError, IndexError):
                        continue
                    if value in Bet.Selection.values:
                        selections.append({
                            'match': match_id,
                            'selection': value,
                        })

            if len(selections) < 2:
                raise ValidationError('A parlay bet requires at least two selections.')

            parlay = place_parlay_bet(
                user=request.user,
                stake=stake,
                selections=selections,
            )
            return redirect('web:parlay_history')
        except SERVICE_ERRORS as exc:
            error = _error_message(exc)
        except (InvalidOperation, ValueError) as exc:
            error = str(exc)

    return render(
        request,
        'web/parlay_bet.html',
        {
            'matches': matches,
            'error': error,
            'active': 'parlay_bet',
        },
    )

PARLAY_STATUS_FILTERS = {
    'open': (ParlayBet.Status.PENDING, 'Open', 'At least one leg hasn’t been settled yet.'),
    'won': (ParlayBet.Status.WON, 'Won', 'Every leg won — payout has been added to your wallet.'),
    'lost': (ParlayBet.Status.LOST, 'Lost', 'At least one leg lost, so the whole parlay lost.'),
    'cancelled': (ParlayBet.Status.REFUNDED, 'Cancelled', 'A leg was cancelled and no leg lost — your stake was refunded in full.'),
}


@login_required
def parlay_history_view(request):
    status_filter = request.GET.get('status', 'all').strip()
    parlays = ParlayBet.objects.filter(user=request.user).prefetch_related('legs__match').order_by('-created_at')
    if status_filter in PARLAY_STATUS_FILTERS:
        parlays = parlays.filter(status=PARLAY_STATUS_FILTERS[status_filter][0])

    paginator = Paginator(parlays, 25)
    parlays_page = paginator.get_page(request.GET.get('page'))

    return render(
        request,
        'web/parlay_history.html',
        {
            'parlays': parlays_page,
            'page_obj': parlays_page,
            'status_filter': status_filter,
            'status_filters': PARLAY_STATUS_FILTERS,
            'status_description': PARLAY_STATUS_FILTERS.get(status_filter, (None, None, None))[2],
            'active': 'parlay_history',
        },
    )

def _wallet_context(user, extra=None):
    from apps.rewards.services import get_lifetime_deposit_total

    wallet, _ = Wallet.objects.get_or_create(user=user)
    transactions = WalletTransaction.objects.filter(wallet=wallet).select_related('wallet')
    pending_deposits = CryptoPayment.objects.filter(
        user=user,
        payment_type=CryptoPayment.PaymentType.DEPOSIT,
        status=CryptoPayment.Status.PENDING,
    )

    lifetime_deposits = get_lifetime_deposit_total(wallet)
    claimed_package_ids = set(UserRewardClaim.objects.filter(user=user).values_list('package_id', flat=True))
    reward_packages = [
        {'package': pkg, 'claimed': pkg.id in claimed_package_ids}
        for pkg in RewardPackage.objects.filter(is_active=True).order_by('deposit_threshold')
    ]

    context = {
        'wallet': wallet,
        'transactions': transactions,
        'pending_deposits': pending_deposits,
        'cashback_credits': CashbackCredit.objects.filter(user=user).order_by('-created_at')[:25],
        'reward_packages': reward_packages,
        'lifetime_deposits': lifetime_deposits,
        'active': 'wallet',
        # The self-confirm button must only ever be usable when the mock
        # provider is active (local/dev). It is never shown against a real
        # payment provider — deposits there are only ever confirmed by a
        # verified provider webhook.
        'dev_confirm_available': get_payment_provider().name == 'mock',
    }
    if extra:
        context.update(extra)
    return context


@login_required
def wallet_view(request):
    return render(request, 'web/wallet.html', _wallet_context(request.user))


@login_required
@require_POST
def wallet_deposit_view(request):
    try:
        amount = Decimal(request.POST.get('amount', '0'))
        if amount <= 0:
            raise ValueError('Must be positive')
        create_deposit(user=request.user, amount=amount, currency='USDT')
        return redirect('web:wallet')
    except (ValueError, InvalidOperation):
        return render(
            request,
            'web/wallet.html',
            _wallet_context(request.user, {'error': 'Invalid deposit amount.'}),
        )


@login_required
@require_POST
def wallet_withdraw_view(request):
    try:
        amount = Decimal(request.POST.get('amount', '0'))
        address = request.POST.get('address', '').strip()
        if not address:
            raise ValueError('Address required')
        create_withdrawal_request(
            user=request.user,
            amount=amount,
            address=address,
            currency='USDT',
        )
        create_audit_log(
            user=request.user,
            action='withdrawal_requested',
            target_type='user',
            target_id=request.user.id,
            metadata={'amount': str(amount), 'address': address},
            ip_address=_get_ip(request),
        )
        return redirect('web:wallet')
    except (ValueError, InvalidOperation):
        return render(
            request,
            'web/wallet.html',
            _wallet_context(request.user, {'error': 'Invalid withdrawal amount.'}),
        )
    except SERVICE_ERRORS as exc:
        return render(
            request,
            'web/wallet.html',
            _wallet_context(request.user, {'error': _error_message(exc)}),
        )


@login_required
@require_POST
def confirm_deposit_view(request, deposit_id):
    """
    Dev/testing convenience ONLY. Real deposits are confirmed exclusively by
    a signature-verified provider webhook (apps.payments.views.PaymentWebhookView).
    This view must never be usable against a real payment provider — it is
    hard-blocked below whenever PAYMENT_PROVIDER isn't 'mock', regardless of
    what URL is hit or what DEBUG is set to, because that setting is exactly
    the kind of thing a deployment can get wrong.
    """
    if get_payment_provider().name != 'mock':
        return render(
            request,
            'web/wallet.html',
            _wallet_context(
                request.user,
                {'error': 'Deposits can only be confirmed by the payment provider.'},
            ),
            status=403,
        )

    deposit = get_object_or_404(
        CryptoPayment,
        pk=deposit_id,
        user=request.user,
        payment_type=CryptoPayment.PaymentType.DEPOSIT,
        status=CryptoPayment.Status.PENDING,
    )

    try:
        handle_deposit_success(deposit.external_id)
        create_audit_log(
            user=request.user,
            action='dev_deposit_self_confirmed',
            target_type='crypto_payment',
            target_id=deposit.id,
            metadata={'amount': str(deposit.amount), 'provider': 'mock'},
            ip_address=_get_ip(request),
        )
        return redirect('web:wallet')
    except SERVICE_ERRORS as exc:
        return render(
            request,
            'web/wallet.html',
            _wallet_context(request.user, {'error': _error_message(exc)}),
        )

BET_STATUS_FILTERS = {
    # query param value -> (Bet.Status value, user-facing label, explanation)
    'open': (Bet.Status.PENDING, 'Open', 'The match hasn’t finished yet, or the result hasn’t been settled.'),
    'won': (Bet.Status.WON, 'Won', 'This selection won — payout has been added to your wallet.'),
    'lost': (Bet.Status.LOST, 'Lost', 'This selection did not win.'),
    'cancelled': (Bet.Status.REFUNDED, 'Cancelled', 'The match was cancelled — your stake was refunded in full.'),
}


@login_required
def exchange_history_view(request):
    orders = ExchangeOrder.objects.filter(user=request.user).select_related('match', 'match__sport').order_by('-created_at')
    paginator = Paginator(orders, 25)
    orders_page = paginator.get_page(request.GET.get('page'))

    from django.db.models import Q as _Q
    fills = ExchangeFill.objects.filter(
        _Q(back_order__user=request.user) | _Q(lay_order__user=request.user)
    ).select_related('match', 'back_order', 'lay_order', 'back_order__user', 'lay_order__user').order_by('-created_at')[:100]

    return render(
        request,
        'web/exchange_history.html',
        {
            'orders_page': orders_page,
            'fills': fills,
            'active': 'exchange_history',
        },
    )


@login_required
def bet_history_view(request):
    status_filter = request.GET.get('status', 'all').strip()
    bets = Bet.objects.filter(user=request.user).select_related('match', 'match__sport').order_by('-created_at')
    if status_filter in BET_STATUS_FILTERS:
        bets = bets.filter(status=BET_STATUS_FILTERS[status_filter][0])

    paginator = Paginator(bets, 25)
    bets_page = paginator.get_page(request.GET.get('page'))

    return render(
        request,
        'web/bet_history.html',
        {
            'bets': bets_page,
            'page_obj': bets_page,
            'status_filter': status_filter,
            'status_filters': BET_STATUS_FILTERS,
            'status_description': BET_STATUS_FILTERS.get(status_filter, (None, None, None))[2],
            'active': 'bets',
        },
    )
