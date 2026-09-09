from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class CasinoConfig(models.Model):
    """
    Singleton settings row (same get_solo()/pk=1 pattern as
    apps.sports.models.OddsAdjustmentConfig etc.) - admin-editable via
    Django admin, no redeploy needed to change it. Off by default: shipping
    this app changes nothing for real users until an admin explicitly
    enables it, same "safe until opted in" posture as HouseLiquidityConfig.
    """
    is_enabled = models.BooleanField(
        default=False,
        verbose_name='Casino enabled (players can launch real-money games)',
    )
    max_launch_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('100.00'),
        validators=[MinValueValidator(Decimal('1.00')), MaxValueValidator(Decimal('100000.00'))],
        verbose_name='Max balance (USD) a single game launch may hand over',
    )
    provider_currency_code = models.CharField(
        max_length=10,
        default='USD',
        verbose_name='Currency code sent to the casino provider (must match what the provider account actually supports - '
                     'confirmed 2026-09-08 that the account is fixed to USD, not admin-selectable per launch)',
    )
    usd_to_provider_currency_rate = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=Decimal('1.0000'),
        validators=[MinValueValidator(Decimal('0.0001')), MaxValueValidator(Decimal('10000.0000'))],
        verbose_name='USD -> provider_currency_code conversion rate (1.0000 when provider_currency_code is USD)',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Casino Config'
        verbose_name_plural = 'Casino Config'

    def __str__(self):
        return f'Casino: {"on" if self.is_enabled else "off"}, max launch ${self.max_launch_balance}, rate {self.usd_to_provider_currency_rate} {self.provider_currency_code}'

    @classmethod
    def get_solo(cls) -> 'CasinoConfig':
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class CasinoBrand(models.Model):
    """One row per provider/brand returned by the aggregator's /providers list (e.g. Pragmatic Play)."""
    brand_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=200)
    logo_url = models.URLField(max_length=500, blank=True, default='')
    game_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Casino Brand'
        verbose_name_plural = 'Casino Brands'

    def __str__(self):
        return self.name


class CasinoGame(models.Model):
    """One row per game returned by the aggregator's /games list. game_uid is what's sent to launch/demo calls."""
    game_id = models.PositiveIntegerField()
    game_uid = models.CharField(max_length=150, unique=True)
    brand = models.ForeignKey(CasinoBrand, on_delete=models.CASCADE, related_name='games')
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=100, blank=True, default='')
    logo_url = models.URLField(max_length=500, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Casino Game'
        verbose_name_plural = 'Casino Games'

    def __str__(self):
        return f'{self.name} ({self.brand.name})'


class CasinoSession(models.Model):
    """
    One row per game launch. opened_balance_usd is real money already
    debited from the player's wallet at launch time (the provider - not
    this platform - holds it during play, per the aggregator's own docs).
    fx_rate_applied is snapshotted here (never re-read live) so a later
    admin change to CasinoConfig.usd_to_pkr_rate can never affect an
    already-open session's math - same "snapshot, don't reference"
    discipline as apps.cashback.models.CashbackCredit.
    """
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        CLOSED = 'closed', 'Closed'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='casino_sessions',
    )
    game = models.ForeignKey(CasinoGame, on_delete=models.PROTECT, related_name='sessions')
    opened_balance_usd = models.DecimalField(max_digits=20, decimal_places=8)
    opened_balance_provider_currency = models.DecimalField(max_digits=20, decimal_places=8)
    provider_currency_code = models.CharField(max_length=10)
    fx_rate_applied = models.DecimalField(max_digits=10, decimal_places=4)
    wallet_amount = models.DecimalField(
        max_digits=20, decimal_places=8, default=Decimal('0'),
        verbose_name='Amount actually debited from the wallet, in wallet_currency (see apps.wallet.services.convert_usd_to_wallet_currency) '
                     '- distinct from opened_balance_provider_currency, which is the Waija/SoftAPI account-currency conversion, a separate concern',
    )
    wallet_currency = models.CharField(max_length=3, default='USD')
    wallet_fx_rate_applied = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal('1.0000'))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    launched_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-launched_at']
        verbose_name = 'Casino Session'
        verbose_name_plural = 'Casino Sessions'

    def __str__(self):
        return f'{self.user.email} - {self.game.name} - {self.opened_balance_usd}'


class CasinoRoundSettlement(models.Model):
    """
    One row per settle-notify callback from the provider.
    provider_serial_number is the idempotency key (unique=True) - a
    retried/duplicate delivery of the same round is rejected at the DB
    level rather than re-credited, following the exchange app's
    lock-then-check-status idiom rather than the race-prone
    check-then-write pattern used by the older NOWPayments webhook.
    """
    session = models.ForeignKey(CasinoSession, on_delete=models.PROTECT, related_name='round_settlements')
    provider_serial_number = models.CharField(max_length=255, unique=True)
    bet_amount = models.DecimalField(max_digits=20, decimal_places=8, default=Decimal('0'))
    win_amount = models.DecimalField(max_digits=20, decimal_places=8, default=Decimal('0'))
    credit_amount_reported = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Casino Round Settlement'
        verbose_name_plural = 'Casino Round Settlements'

    def __str__(self):
        return f'{self.session_id} - {self.provider_serial_number}'


class CasinoWalletEvent(models.Model):
    """
    One row per live wallet callback from a true seamless-wallet provider
    (Waija - see providers.WaijaProvider.requires_upfront_balance=False).
    Unlike CasinoRoundSettlement (one combined bet+win report per round,
    tied to a CasinoSession that was pre-funded at launch), each of these
    is an independent debit or credit against the wallet's live balance,
    not tied to any session-level reservation. call_id is the idempotency
    key (unique=True) - Waija's own per-event reference, not per-round.
    """
    class Action(models.TextChoices):
        DEBIT = 'debit', 'Debit'
        CREDIT = 'credit', 'Credit'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='casino_wallet_events',
    )
    call_id = models.CharField(max_length=255, unique=True)
    action = models.CharField(max_length=10, choices=Action.choices)
    amount = models.DecimalField(
        max_digits=20, decimal_places=8,
        verbose_name='Raw amount as reported by the provider, in the provider account currency (USD today) - kept '
                     'unchanged for reconciliation against the provider\'s own dashboard; see wallet_amount for what '
                     'was actually applied to the wallet',
    )
    wallet_amount = models.DecimalField(
        max_digits=20, decimal_places=8, default=Decimal('0'),
        verbose_name='Amount actually applied to wallet.balance, in wallet_currency (see apps.wallet.services.convert_usd_to_wallet_currency)',
    )
    wallet_currency = models.CharField(max_length=3, default='USD')
    wallet_fx_rate_applied = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal('1.0000'))
    round_id = models.CharField(max_length=255, blank=True, default='')
    game_uid = models.CharField(max_length=150, blank=True, default='')
    game = models.ForeignKey(
        CasinoGame, on_delete=models.SET_NULL, null=True, blank=True, related_name='wallet_events',
        verbose_name='Resolved from game_uid at write time, for admin/ledger display - never used for money math itself.',
    )
    event_type = models.CharField(max_length=30, blank=True, default='')
    is_rollback = models.BooleanField(default=False)
    cash_skipped = models.BooleanField(default=False, verbose_name='True for a free-round (bonus_fs) debit that never touched real cash')
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Casino Wallet Event'
        verbose_name_plural = 'Casino Wallet Events'

    def __str__(self):
        return f'{self.user_id} - {self.action} {self.amount} ({self.call_id})'
