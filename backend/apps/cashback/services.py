from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.wallet.models import Wallet, WalletTransaction

from .models import CashbackConfig, CashbackCredit


def get_effective_settings(user):
    """
    Resolves per-user overrides against the global CashbackConfig. A `None`
    override means "inherit the global value" - the same convention already
    used by User.min_bet_amount/max_bet_amount elsewhere in this project.
    Returns (enabled, rate, multiplier, deduct_original_on_unlock).
    """
    config = CashbackConfig.get_solo()

    enabled = config.is_enabled if user.cashback_enabled_override is None else user.cashback_enabled_override
    rate = config.default_rate if user.cashback_rate_override is None else user.cashback_rate_override
    multiplier = (
        config.default_wagering_multiplier
        if user.cashback_wagering_multiplier_override is None
        else user.cashback_wagering_multiplier_override
    )
    return enabled, rate, multiplier, config.deduct_original_on_unlock


def credit_cashback_for_loss(user, loss_amount: Decimal, source_description: str):
    """
    Credits a percentage of one losing bet/parlay/exchange-fill to the user,
    if cashback is enabled for them. Mirrors
    apps.bets.services._credit_affiliate_commission() - self-locks the
    wallet, no-ops cleanly when disabled or the computed amount is zero.

    Every rule used to compute this credit (rate, multiplier,
    deduct_original_on_unlock) is snapshotted onto the created row so a
    later change to CashbackConfig or a user's override can never
    retroactively change an already-issued credit.
    """
    enabled, rate, multiplier, deduct_on_unlock = get_effective_settings(user)
    if not enabled or rate <= 0 or loss_amount <= 0:
        return None

    cashback_amount = (loss_amount * rate / Decimal('100')).quantize(Decimal('0.00000001'))
    if cashback_amount <= 0:
        return None

    wagering_required = (cashback_amount * multiplier).quantize(Decimal('0.00000001'))
    status = CashbackCredit.Status.LOCKED if wagering_required > 0 else CashbackCredit.Status.UNLOCKED

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=user)
        wallet.balance += cashback_amount
        if status == CashbackCredit.Status.LOCKED:
            wallet.locked_cashback_balance += cashback_amount
        wallet.save(update_fields=['balance', 'locked_cashback_balance', 'updated_at'])

        credit = CashbackCredit.objects.create(
            user=user,
            source_description=source_description,
            loss_amount=loss_amount,
            rate_applied=rate,
            cashback_amount=cashback_amount,
            multiplier_applied=multiplier,
            wagering_required=wagering_required,
            deduct_original_on_unlock=deduct_on_unlock,
            status=status,
            unlocked_at=timezone.now() if status == CashbackCredit.Status.UNLOCKED else None,
        )

        WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.TxnType.CASHBACK_CREDIT,
            amount=cashback_amount,
            status=WalletTransaction.Status.COMPLETED,
            balance_after=wallet.balance,
            description=f'Cashback credit – {source_description}',
        )

    return credit


def record_wager(wallet: Wallet, amount: Decimal):
    """
    Must be called with `wallet` already locked (select_for_update) by the
    caller, from inside its existing atomic() block - mirrors how
    _credit_affiliate_commission is called from inside settle_bet's already
    -open lock, so this adds no new locking of its own beyond the
    CashbackCredit rows it touches.

    Every currently-LOCKED credit for this wallet's user gets the FULL
    stake amount added to its own wagering progress independently (not
    split or applied FIFO across multiple open credits) - simpler to
    implement correctly, and never less generous to the user than an
    allocation scheme would be.
    """
    if amount <= 0:
        return

    locked_credits = list(
        CashbackCredit.objects.select_for_update().filter(
            user_id=wallet.user_id, status=CashbackCredit.Status.LOCKED,
        )
    )
    if not locked_credits:
        return

    wallet_changed = False
    for credit in locked_credits:
        credit.wagering_progress = min(credit.wagering_required, credit.wagering_progress + amount)
        if credit.wagering_progress < credit.wagering_required:
            credit.save(update_fields=['wagering_progress'])
            continue

        credit.status = CashbackCredit.Status.UNLOCKED
        credit.unlocked_at = timezone.now()
        wallet.locked_cashback_balance -= credit.cashback_amount
        wallet_changed = True

        if credit.deduct_original_on_unlock:
            new_balance = max(wallet.balance - credit.cashback_amount, Decimal('0'))
            deduction = wallet.balance - new_balance
            wallet.balance = new_balance
            if deduction > 0:
                WalletTransaction.objects.create(
                    wallet=wallet,
                    txn_type=WalletTransaction.TxnType.CASHBACK_CLAWBACK,
                    amount=-deduction,
                    status=WalletTransaction.Status.COMPLETED,
                    balance_after=wallet.balance,
                    description=f'Cashback clawback on unlock – {credit.source_description}',
                )

        credit.save(update_fields=['wagering_progress', 'status', 'unlocked_at'])

    if wallet_changed:
        wallet.save(update_fields=['balance', 'locked_cashback_balance', 'updated_at'])
