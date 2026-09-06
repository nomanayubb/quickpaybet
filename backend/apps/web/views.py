from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import authenticate
from django.core.cache import cache
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
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

from apps.accounts.models import User, PasswordResetToken
from apps.accounts.permissions import roles_assignable_by, user_can_manage_target
from apps.audit.models import AuditLog
from apps.audit.services import create_audit_log
from apps.bets.models import Bet, ParlayBet
from apps.bets.services import place_bet, place_parlay_bet, refund_bet, settle_bets_for_match
from apps.sports.models import Match, Sport, Tournament
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


def admin_users_view(request):
    if not _has_dashboard_access(request.user):
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")

    users_list = User.objects.select_related('parent').order_by('email')
    if not _is_full_admin(request.user):
        # A master only manages/sees their own direct downstream users.
        users_list = users_list.filter(parent_id=request.user.id)
    paginator = Paginator(users_list, 20)
    page_number = request.GET.get('page')
    users = paginator.get_page(page_number)

    return render(
        request,
        'web/admin_users.html',
        {
            'users': users,
            'page_obj': users,
            'active': 'admin_users',
        },
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

            user.role = new_role

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
    matches_list = Match.objects.select_related('sport', 'tournament').order_by('start_time')
    paginator = Paginator(matches_list, 20)
    page_number = request.GET.get('page')
    matches = paginator.get_page(page_number)

    return render(
        request,
        'web/matches.html',
        {
            'matches': matches,
            'page_obj': matches,
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
        except SERVICE_ERRORS as exc:
            place_error = _error_message(exc)
        except InvalidOperation:
            place_error = 'Invalid stake.'
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

def _wallet_context(user, extra=None):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    transactions = WalletTransaction.objects.filter(wallet=wallet).select_related('wallet')
    pending_deposits = CryptoPayment.objects.filter(
        user=user,
        payment_type=CryptoPayment.PaymentType.DEPOSIT,
        status=CryptoPayment.Status.PENDING,
    )
    context = {
        'wallet': wallet,
        'transactions': transactions,
        'pending_deposits': pending_deposits,
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

@login_required
def bet_history_view(request):
    bets = Bet.objects.filter(user=request.user).select_related('match', 'match__sport')
    return render(request, 'web/bet_history.html', {'bets': bets, 'active': 'bets'})
