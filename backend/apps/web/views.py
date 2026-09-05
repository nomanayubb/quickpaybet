from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import authenticate
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError

from django.conf import settings
from decimal import Decimal, InvalidOperation

from apps.accounts.models import User
from apps.bets.models import Bet
from apps.bets.services import place_bet, refund_bet, settle_bets_for_match
from apps.sports.models import Match
from apps.wallet.models import Wallet, WalletTransaction
from apps.wallet.services import deposit_funds, withdraw_funds
from apps.payments.models import CryptoPayment
from apps.payments.services import create_deposit
from apps.reports.services import get_overview_report


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

    pending_bets = Bet.objects.filter(match=match, status=Bet.Status.PENDING)
    for bet in pending_bets:
        refund_bet(bet)

    match.status = Match.Status.CANCELLED
    match.save()

    return redirect('web:admin_matches')


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

        user = authenticate(request, email=email, password=password)
        if user is not None:
            auth_login(request, user)
            return redirect('web:home')
        return render(
            request,
            'web/login.html',
            {'error': 'Invalid email or password.'},
        )

    return render(request, 'web/login.html')


def logout_view(request):
    auth_logout(request)
    return redirect('web:home')


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
def bet_history_view(request):
    bets = Bet.objects.filter(user=request.user).select_related('match', 'match__sport')
    return render(request, 'web/bet_history.html', {'bets': bets, 'active': 'bets'})
