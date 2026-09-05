from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import authenticate
from django.core.cache import cache
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from datetime import timedelta

from django.conf import settings
from decimal import Decimal, InvalidOperation

from apps.accounts.models import User, PasswordResetToken
from apps.audit.models import AuditLog
from apps.bets.models import Bet, ParlayBet
from apps.bets.services import place_bet, place_parlay_bet, refund_bet, settle_bets_for_match
from apps.sports.models import Match, Sport, Tournament
from apps.wallet.models import Wallet, WalletTransaction
from apps.wallet.services import deposit_funds, withdraw_funds
from apps.payments.models import CryptoPayment
from apps.payments.services import create_deposit, handle_deposit_success
from apps.reports.services import get_overview_report, get_daily_report


def _has_dashboard_access(user):
    if not user.is_authenticated:
        return False
    return user.is_staff or user.role in (User.Role.ADMIN, User.Role.MASTER)


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

    return render(
        request,
        'web/admin_reports.html',
        {
            'report': report,
            'daily': daily,
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
            error = str(exc)
        except Exception as exc:
            error = str(exc)

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
                if d <= 0:
                    raise ValidationError('Odds must be positive.')
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
            error = str(exc)

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

    matches = Match.objects.select_related('sport', 'tournament').order_by('start_time')
    return render(
        request,
        'web/admin_matches.html',
        {
            'matches': matches,
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
                if d <= 0:
                    raise ValidationError('Odds must be positive.')
                return d

            match.home_team = home_team
            match.away_team = away_team
            match.status = status
            match.start_time = start_time
            match.odds_home = parse_decimal(odds_home_raw)
            match.odds_draw = parse_decimal(odds_draw_raw)
            match.odds_away = parse_decimal(odds_away_raw)

            match.save()
            return redirect('web:admin_matches')
        except (ValidationError, InvalidOperation, ValueError) as exc:
            error = str(exc)

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

    return redirect('web:admin_matches')


def admin_audit_logs_view(request):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    logs = AuditLog.objects.select_related('user').order_by('-created_at')[:200]
    return render(
        request,
        'web/admin_audit.html',
        {
            'logs': logs,
            'active': 'audit',
        },
    )


def admin_users_view(request):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    users = User.objects.select_related('parent').order_by('email')
    return render(
        request,
        'web/admin_users.html',
        {'users': users, 'active': 'admin_users'},
    )


def admin_user_update_view(request, user_id):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    user = get_object_or_404(User, pk=user_id)
    possible_parents = User.objects.exclude(pk=user.pk).order_by('email')

    if request.method == 'POST':
        try:
            new_role = request.POST.get('role')
            if new_role not in User.Role.values:
                raise ValidationError('Invalid role.')

            parent_raw = request.POST.get('parent_id', '').strip()
            min_bet_raw = request.POST.get('min_bet_amount', '').strip()
            max_bet_raw = request.POST.get('max_bet_amount', '').strip()
            is_betting_enabled = request.POST.get('is_betting_enabled') == 'on'

            user.role = new_role

            if parent_raw:
                try:
                    user.parent_id = int(parent_raw)
                except (TypeError, ValueError):
                    raise ValidationError('Invalid parent user.')
            else:
                user.parent = None

            user.min_bet_amount = Decimal(min_bet_raw) if min_bet_raw else None
            user.max_bet_amount = Decimal(max_bet_raw) if max_bet_raw else None

            if user.min_bet_amount is not None and user.min_bet_amount < 0:
                raise ValidationError('Minimum bet cannot be negative.')
            if user.max_bet_amount is not None and user.max_bet_amount < 0:
                raise ValidationError('Maximum bet cannot be negative.')

            user.is_betting_enabled = is_betting_enabled
            user.save()

            return redirect('web:admin_users')
        except (ValidationError, InvalidOperation, ValueError) as exc:
            return render(
                request,
                'web/admin_user_edit.html',
                {
                    'error': str(exc),
                    'edit_user': user,
                    'possible_parents': possible_parents,
                },
            )

    return render(
        request,
        'web/admin_user_edit.html',
        {
            'edit_user': user,
            'possible_parents': possible_parents,
        },
    )


def home_view(request):
    matches = Match.objects.select_related('sport', 'tournament').filter(
        status__in=[Match.Status.SCHEDULED, Match.Status.LIVE]
    )[:20]
    return render(request, 'web/home.html', {'matches': matches, 'active': 'home'})


def register_view(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
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

        user = User.objects.create_user(email=email, password=password)
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


def logout_view(request):
    auth_logout(request)
    return redirect('web:home')


def password_reset_request_view(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        try:
            user = User.objects.get(email=email)
            PasswordResetToken.objects.filter(
                user=user,
                is_used=False,
            ).update(is_used=True)

            import secrets
            token = secrets.token_urlsafe(40)
            PasswordResetToken.objects.create(user=user, token=token)

            return render(
                request,
                'web/reset_password_done.html',
                {'token': token},
            )
        except User.DoesNotExist:
            pass

        return render(
            request,
            'web/reset_password_done.html',
            {},
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
                reset_obj = PasswordResetToken.objects.get(
                    token=token,
                    is_used=False,
                )
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
    matches = Match.objects.select_related('sport', 'tournament').all()
    return render(request, 'web/matches.html', {'matches': matches, 'active': 'matches'})


def match_detail_view(request, pk):
    match = get_object_or_404(Match.objects.select_related('sport', 'tournament'), pk=pk)
    place_error = None

    if request.method == 'POST' and request.user.is_authenticated:
        try:
            selection = request.POST.get('selection')
            stake_raw = request.POST.get('stake')
            if not selection or not stake_raw:
                raise ValidationError('Please provide selection and stake.')

            stake = Decimal(stake_raw)
            bet = place_bet(
                user=request.user,
                match_id=match.id,
                selection=selection,
                stake=stake,
            )
            return redirect('web:match_detail', pk=match.pk)
        except (ValidationError, InvalidOperation) as exc:
            place_error = exc if isinstance(exc, ValidationError) else ValidationError('Invalid stake.')
    elif request.method == 'POST' and not request.user.is_authenticated:
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    return render(
        request,
        'web/match_detail.html',
        {
            'match': match,
            'error': place_error,
            'active': 'matches',
        },
    )


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
        except (ValidationError, InvalidOperation) as exc:
            return render(
                request,
                'web/match_detail.html',
                {
                    'match': match,
                    'error': exc if isinstance(exc, ValidationError) else ValidationError('Invalid stake.'),
                },
            )
    return redirect('web:match_detail', pk=match.pk)


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
        except (ValidationError, InvalidOperation, ValueError) as exc:
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


@login_required
def parlay_history_view(request):
    parlays = ParlayBet.objects.filter(user=request.user).prefetch_related('legs__match')
    return render(
        request,
        'web/parlay_history.html',
        {
            'parlays': parlays,
            'active': 'parlay_history',
        },
    )


@login_required
def wallet_view(request):
    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    transactions = WalletTransaction.objects.filter(wallet=wallet).select_related('wallet')
    pending_deposits = CryptoPayment.objects.filter(
        user=request.user,
        payment_type=CryptoPayment.PaymentType.DEPOSIT,
        status=CryptoPayment.Status.PENDING,
    )
    return render(
        request,
        'web/wallet.html',
        {
            'wallet': wallet,
            'transactions': transactions,
            'pending_deposits': pending_deposits,
            'active': 'wallet',
        },
    )


@login_required
@require_POST
def wallet_deposit_view(request):
    try:
        amount = Decimal(request.POST.get('amount', '0'))
        if amount <= 0:
            raise ValueError('Must be positive')
        payment = create_deposit(user=request.user, amount=amount, currency='USDT')
        return redirect('web:wallet')
    except (ValueError, InvalidOperation):
        return render(
            request,
            'web/wallet.html',
            {'error': 'Invalid deposit amount.'},
        )


@login_required
@require_POST
def wallet_withdraw_view(request):
    try:
        amount = Decimal(request.POST.get('amount', '0'))
        address = request.POST.get('address', '').strip()
        if not address:
            raise ValueError('Address required')
        withdraw_funds(
            user=request.user,
            amount=amount,
            description=f'Withdrawal to {address}',
        )
        return redirect('web:wallet')
    except (ValueError, ValidationError, InvalidOperation) as exc:
        return render(
            request,
            'web/wallet.html',
            {'error': str(exc)},
        )


@login_required
@require_POST
def confirm_deposit_view(request, deposit_id):
    deposit = get_object_or_404(
        CryptoPayment,
        pk=deposit_id,
        user=request.user,
        payment_type=CryptoPayment.PaymentType.DEPOSIT,
        status=CryptoPayment.Status.PENDING,
    )

    try:
        handle_deposit_success(deposit.external_id)
        return redirect('web:wallet')
    except ValidationError as exc:
        return render(
            request,
            'web/wallet.html',
            {'error': str(exc)},
        )


@login_required
def bet_history_view(request):
    bets = Bet.objects.filter(user=request.user).select_related('match', 'match__sport')
    return render(request, 'web/bet_history.html', {'bets': bets, 'active': 'bets'})
