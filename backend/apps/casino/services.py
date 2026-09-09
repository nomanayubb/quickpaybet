from decimal import Decimal

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from apps.audit.services import create_audit_log
from apps.wallet.models import Wallet, WalletTransaction

from .models import CasinoBrand, CasinoConfig, CasinoGame, CasinoRoundSettlement, CasinoSession, CasinoWalletEvent
from .providers import get_casino_provider


class InsufficientCasinoBalance(Exception):
    """Raised by handle_waija_wallet_event on a debit the wallet can't cover - carries the current balance so the caller can report it back verbatim."""

    def __init__(self, balance: Decimal):
        self.balance = balance
        super().__init__(f'Insufficient balance: {balance}')


def launch_game(user, game: CasinoGame, requested_usd_amount: Decimal | None, return_url: str, callback_url: str, language: str = 'en'):
    """
    Debits the player's wallet by `requested_usd_amount` and opens a
    provider game session. The provider call is a network request, so it
    is deliberately made OUTSIDE any DB transaction: the amount is first
    reserved (like apps.exchange.services.place_order), then converted
    into a real debit only after the provider confirms the launch - a
    failed/erroring provider call releases the reservation and nothing is
    lost.

    currency_code/provider_currency_code use CasinoConfig.provider_currency_code
    (USD by default) rather than a hardcoded PKR conversion - a live check
    against the real SoftAPI account (2026-09-08) showed its play_currency
    is fixed to USD at the account level, not choosable per launch call.
    The USD<->provider-currency rate is still snapshotted onto the session
    (1.0000 when the provider currency IS USD) so switching this config
    later never affects an already-open session's math.

    Branches on provider.requires_upfront_balance (see
    apps.casino.providers.BaseCasinoProvider): SoftAPI needs a starting
    balance handed over at launch (the lump-sum path below); a genuine
    seamless-wallet provider like Waija does not - it queries/debits/
    credits the real wallet live via its own callbacks, so launching one
    must never touch the wallet at all, or the player would be charged
    twice (once here, again per spin). `requested_usd_amount` is ignored
    entirely for a seamless provider.
    """
    config = CasinoConfig.get_solo()
    if not config.is_enabled:
        raise ValidationError('Casino is currently disabled.')
    if not game.is_active or not game.brand.is_active:
        raise ValidationError('This game is not currently available.')

    provider = get_casino_provider()
    rate = config.usd_to_provider_currency_rate
    provider_currency_code = config.provider_currency_code

    if not provider.requires_upfront_balance:
        result = provider.launch_game(
            user_id=user.id,
            balance=Decimal('0'),
            game_uid=game.game_uid,
            currency_code=provider_currency_code,
            language=language,
            return_url=return_url,
            callback_url=callback_url,
        )
        session = CasinoSession.objects.create(
            user=user,
            game=game,
            opened_balance_usd=Decimal('0'),
            opened_balance_provider_currency=Decimal('0'),
            provider_currency_code=provider_currency_code,
            fx_rate_applied=rate,
        )
        create_audit_log(
            user=user, action='casino_session_launched', target_type='casino_session', target_id=session.id,
            metadata={'game': game.name, 'seamless_wallet': True},
        )
        return session, result['url']

    if requested_usd_amount is None or requested_usd_amount <= 0:
        raise ValidationError('Amount must be positive.')
    if requested_usd_amount > config.max_launch_balance:
        raise ValidationError(f'Amount exceeds the maximum allowed per launch (${config.max_launch_balance}).')

    provider_amount = (requested_usd_amount * rate).quantize(Decimal('0.01'))

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=user)
        if wallet.available_balance < requested_usd_amount:
            raise ValidationError('Insufficient available balance.')
        wallet.reserved_balance += requested_usd_amount
        wallet.save(update_fields=['reserved_balance', 'updated_at'])

    try:
        result = provider.launch_game(
            user_id=user.id,
            balance=provider_amount,
            game_uid=game.game_uid,
            currency_code=provider_currency_code,
            language=language,
            return_url=return_url,
            callback_url=callback_url,
        )
    except Exception:
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=user)
            wallet.reserved_balance -= requested_usd_amount
            wallet.save(update_fields=['reserved_balance', 'updated_at'])
        raise

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=user)
        wallet.reserved_balance -= requested_usd_amount
        wallet.balance -= requested_usd_amount
        wallet.save(update_fields=['balance', 'reserved_balance', 'updated_at'])

        session = CasinoSession.objects.create(
            user=user,
            game=game,
            opened_balance_usd=requested_usd_amount,
            opened_balance_provider_currency=provider_amount,
            provider_currency_code=provider_currency_code,
            fx_rate_applied=rate,
        )
        WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.TxnType.CASINO_SESSION_OPEN,
            amount=-requested_usd_amount,
            status=WalletTransaction.Status.COMPLETED,
            balance_after=wallet.balance,
            reference_id=f'casino-session-{session.id}',
            description=f'Casino launch - {game.name}',
        )

    create_audit_log(
        user=user, action='casino_session_launched', target_type='casino_session', target_id=session.id,
        metadata={'game': game.name, 'amount_usd': str(requested_usd_amount), 'amount_provider_currency': str(provider_amount)},
    )
    return session, result['url']


def handle_round_settlement(*, member_account: str, game_uid: str, bet_amount, win_amount,
                             credit_amount, serial_number: str, raw_payload: dict):
    """
    Applies one settle-notify callback. Idempotent: locks the matching
    OPEN session first (serializing any concurrent/retried delivery for
    that same session), then checks whether this serial_number has
    already been recorded - a duplicate delivery is a safe no-op, never a
    second credit. Mirrors apps.exchange.services._settle_fill's
    lock-then-check-status idiom rather than the older, race-prone
    check-then-write pattern used by the NOWPayments webhook.

    Only `win_amount` is credited back to the wallet - `bet_amount` was
    already taken out of the wallet in full at launch time (launch_game
    debits the whole session balance up front, since the provider holds
    it during play), so crediting bet_amount again here would double-count.
    """
    from apps.accounts.models import User

    try:
        user = User.objects.get(pk=int(member_account))
    except (User.DoesNotExist, ValueError, TypeError):
        raise ValidationError('Unknown member_account.')
    try:
        game = CasinoGame.objects.get(game_uid=str(game_uid))
    except CasinoGame.DoesNotExist:
        raise ValidationError('Unknown game_uid.')

    bet_amount = Decimal(str(bet_amount or '0'))
    win_amount = Decimal(str(win_amount or '0'))
    credit_amount_decimal = Decimal(str(credit_amount)) if credit_amount is not None else None

    with transaction.atomic():
        session = (
            CasinoSession.objects.select_for_update()
            .filter(user=user, game=game, status=CasinoSession.Status.OPEN)
            .order_by('-launched_at')
            .first()
        )
        if session is None:
            raise ValidationError('No open casino session found for this user/game.')

        if CasinoRoundSettlement.objects.filter(provider_serial_number=serial_number).exists():
            return  # already processed - safe to call more than once

        win_amount_usd = (win_amount / session.fx_rate_applied).quantize(Decimal('0.00000001'))

        try:
            CasinoRoundSettlement.objects.create(
                session=session,
                provider_serial_number=serial_number,
                bet_amount=bet_amount,
                win_amount=win_amount,
                credit_amount_reported=credit_amount_decimal,
                raw_payload=raw_payload,
            )
        except IntegrityError:
            return  # duplicate serial_number raced in - already processed

        if win_amount_usd > 0:
            wallet = Wallet.objects.select_for_update().get(user=user)
            wallet.balance += win_amount_usd
            wallet.save(update_fields=['balance', 'updated_at'])
            WalletTransaction.objects.create(
                wallet=wallet,
                txn_type=WalletTransaction.TxnType.CASINO_WIN,
                amount=win_amount_usd,
                status=WalletTransaction.Status.COMPLETED,
                balance_after=wallet.balance,
                reference_id=f'casino-round-{serial_number}',
                description=f'Casino win - {game.name}',
            )

    create_audit_log(
        user=user, action='casino_round_settled', target_type='casino_session', target_id=session.id,
        metadata={'serial_number': serial_number, 'bet_amount': str(bet_amount), 'win_amount': str(win_amount)},
    )


def get_demo_url(game: CasinoGame, return_url: str = '') -> str:
    provider = get_casino_provider()
    return provider.fetch_demo_url(game.game_uid, return_url=return_url)


def sync_catalog() -> dict:
    """
    Pulls the full provider+game catalog and upserts it. New rows default
    is_active=True (client's explicit instruction: sync everything, admin
    curates/hides afterward) - existing rows keep whatever is_active an
    admin already set, since that field is deliberately left out of the
    update_or_create `defaults` below. Manual-trigger only (management
    command) - no automatic timer, matching the same caution already
    applied to ParlayAPI syncing (this provider is also rate-limited).
    """
    provider = get_casino_provider()

    brand_map = {}
    for entry in provider.fetch_providers():
        brand, _ = CasinoBrand.objects.update_or_create(
            brand_id=entry['brand_id'],
            defaults={
                'name': entry['name'],
                'logo_url': entry.get('logo') or '',
                'game_count': entry.get('game_count', 0),
            },
        )
        brand_map[entry['brand_id']] = brand

    games_created = 0
    games_updated = 0
    for entry in provider.fetch_games():
        brand = brand_map.get(entry['brand_id'])
        if brand is None:
            continue
        _, was_created = CasinoGame.objects.update_or_create(
            game_uid=entry['game_uid'],
            defaults={
                'game_id': entry['game_id'],
                'brand': brand,
                'name': entry['name'],
                'category': entry.get('category', ''),
                'logo_url': entry.get('logo') or '',
            },
        )
        games_created += int(was_created)
        games_updated += int(not was_created)

    return {'brands': len(brand_map), 'games_created': games_created, 'games_updated': games_updated}


def _resolve_waija_user(username: str):
    from apps.accounts.models import User
    if not username.startswith('qpb'):
        raise ValidationError('Unknown player.')
    try:
        user_id = int(username[3:])
        return User.objects.get(pk=user_id)
    except (ValueError, User.DoesNotExist):
        raise ValidationError('Unknown player.')


def handle_waija_balance_query(username: str) -> Decimal:
    """No money movement - just reports the live wallet balance for Waija's `balance` callback."""
    user = _resolve_waija_user(username)
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet.balance


def handle_waija_wallet_event(*, username: str, action: str, amount: Decimal, call_id: str,
                               round_id: str, game_uid: str, is_rollback: bool, event_type: str,
                               raw_payload: dict) -> Decimal:
    """
    Applies one live wallet event (debit or credit) from a true
    seamless-wallet provider (Waija). Idempotent on `call_id` (Waija's own
    per-event reference - NOT per-round: one round sends a separate debit
    and credit sharing the same round_id but different call_id, so keying
    on round_id would silently drop every win - see the provider's own
    docs). Serialized by locking the wallet row before checking for a
    duplicate, same idiom as apps.exchange.services._settle_fill, so a
    retried delivery can never double-apply even under real concurrency.
    Returns the wallet's balance after the change (or its unchanged
    current balance, on a duplicate).

    A free-round debit (event_type == 'bonus_fs', action == debit) never
    touches real cash - the provider's docs are explicit that the free
    round was already paid for elsewhere; a win from it still credits
    real cash normally.
    """
    user = _resolve_waija_user(username)
    game = CasinoGame.objects.filter(game_uid=game_uid).select_related('brand').first()
    game_label = f'{game.name} ({game.brand.name})' if game else game_uid

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=user)

        if CasinoWalletEvent.objects.filter(call_id=call_id).exists():
            return wallet.balance  # already applied - safe to call more than once

        cash_skipped = event_type == 'bonus_fs' and action == CasinoWalletEvent.Action.DEBIT

        if not cash_skipped:
            if action == CasinoWalletEvent.Action.DEBIT:
                if wallet.available_balance < amount:
                    raise InsufficientCasinoBalance(wallet.balance)
                wallet.balance -= amount
                txn_type, txn_amount = WalletTransaction.TxnType.CASINO_BET, -amount
            else:
                wallet.balance += amount
                txn_type, txn_amount = WalletTransaction.TxnType.CASINO_WIN, amount
            wallet.save(update_fields=['balance', 'updated_at'])
            WalletTransaction.objects.create(
                wallet=wallet, txn_type=txn_type, amount=txn_amount,
                status=WalletTransaction.Status.COMPLETED, balance_after=wallet.balance,
                reference_id=f'casino-wallet-event-{call_id}',
                description=f'Casino {action} - {game_label}',
            )

        try:
            CasinoWalletEvent.objects.create(
                user=user, call_id=call_id, action=action, amount=amount, round_id=round_id,
                game_uid=game_uid, game=game, event_type=event_type, is_rollback=is_rollback,
                cash_skipped=cash_skipped, raw_payload=raw_payload,
            )
        except IntegrityError:
            pass  # duplicate call_id - the wallet lock above already made this unreachable in practice

    return wallet.balance
