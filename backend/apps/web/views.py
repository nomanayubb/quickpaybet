from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError

from django.conf import settings
from decimal import Decimal, InvalidOperation

from apps.accounts.models import User
from apps.bets.models import Bet
from apps.bets.services import place_bet, refund_bet
from apps.sports.models import Match
from apps.wallet.models import Wallet, WalletTransaction
from apps.wallet.services import deposit_funds, withdraw_funds
from apps.payments.models import CryptoPayment
from apps.payments.services import create_deposit


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
