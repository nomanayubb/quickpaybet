from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple


@dataclass(frozen=True)
class ResolvedPricingMode:
    """
    One resolved "Custom Odds" scope's worth of settings - see
    resolve_match_pricing_mode()/resolve_user_pricing_mode() for how one
    of these gets built, and compute_back_and_lay() for how it's applied.

    Custom back/lay values are per-selection (Home/Draw/Away each get
    their own number) - a match's three outcomes are three different real
    prices, so one shared frozen number for all three would be wrong
    (confirmed live: setting one "custom back" value made Home, Draw, and
    Away all show the identical price on the real exchange order book,
    which is never a sensible market). lay_relative_delta stays a single
    shared value on purpose - it's added to each selection's own back, so
    it already produces a different lay per selection without needing to
    be split three ways itself.
    """
    back_mode: str
    back_custom_value_home: Optional[Decimal]
    back_custom_value_draw: Optional[Decimal]
    back_custom_value_away: Optional[Decimal]
    lay_mode: str
    lay_relative_delta: Optional[Decimal]
    lay_custom_value_home: Optional[Decimal]
    lay_custom_value_draw: Optional[Decimal]
    lay_custom_value_away: Optional[Decimal]

    def back_custom_value_for(self, selection: str) -> Optional[Decimal]:
        return getattr(self, f'back_custom_value_{selection}', None)

    def lay_custom_value_for(self, selection: str) -> Optional[Decimal]:
        return getattr(self, f'lay_custom_value_{selection}', None)


def _to_decimal(value) -> Decimal:
    return Decimal(str(value))


def _to_odds(probability: Decimal) -> Decimal:
    if probability <= 0:
        return Decimal('999.00')
    return (Decimal('1') / probability).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )


def compute_margin(odds_home, odds_draw, odds_away) -> Decimal:
    """Return the bookmaker overround for a 1X2 market.

    A margin of 0.06 means the bookmaker expects to keep 6% of turnover.
    If ``odds_draw`` is ``None``, it treats this as a two-outcome market.
    """
    if odds_draw is None or str(odds_draw).strip() == '':
        raw_odds = [_to_decimal(odds_home), _to_decimal(odds_away)]
    else:
        raw_odds = [
            _to_decimal(odds_home),
            _to_decimal(odds_draw),
            _to_decimal(odds_away),
        ]
    implied = [Decimal('1') / odd for odd in raw_odds]
    return sum(implied) - Decimal('1')


def normalize_odds(
    odds_home,
    odds_draw,
    odds_away,
    margin: Decimal = Decimal('0.05'),
) -> Tuple[Decimal, Optional[Decimal], Decimal]:
    """Remove the bookmaker's overround and reapply a target margin.

    Args:
        odds_home: raw decimal odds for home
        odds_draw: raw decimal odds for draw ; pass ``None`` for two-outcome markets
        odds_away: raw decimal odds for away
        margin: target overround (0.05 means 5%)

    Returns:
        A tuple (home_odds, draw_odds_or_None, away_odds) adjusted to the target margin.
    """
    raw_odds = [_to_decimal(odds_home)]

    has_draw = not (odds_draw is None or str(odds_draw).strip() == '')
    if has_draw:
        raw_odds.append(_to_decimal(odds_draw))

    raw_odds.append(_to_decimal(odds_away))

    implied = [Decimal('1') / odd for odd in raw_odds]
    total_implied = sum(implied)

    if total_implied <= 0:
        raise ValueError('Invalid odds: total implied probability must be positive.')

    target_sum = Decimal('1') + margin
    normalized = []
    for imp in implied:
        fair_prob = imp / total_implied
        adjusted_prob = fair_prob * target_sum
        if adjusted_prob <= 0:
            adjusted_prob = Decimal('0.0001')
        normalized.append(_to_odds(adjusted_prob))

    if not has_draw:
        # Insert None in the draw position for two-way markets.
        normalized.insert(1, None)

    return (
        normalized[0],
        normalized[1],
        normalized[2],
    )


ODDS_FLOOR = Decimal('1.01')


def resolve_effective_adjustment(match_adjustment, default_adjustment) -> Decimal:
    """Returns a match's own odds_adjustment override if set, else the global default."""
    return match_adjustment if match_adjustment is not None else default_adjustment


def apply_odds_adjustment(
    adjustment: Decimal,
    odds_home,
    odds_draw,
    odds_away,
) -> Tuple[Decimal, Optional[Decimal], Decimal]:
    """Adds a flat `adjustment` to each odds value, floor-clamped so the result
    can never reach or drop below 1.00 (the only value this codebase treats
    as valid odds anywhere else). `None` (two-outcome markets have no draw
    odds) always passes through unchanged. A zero adjustment is a no-op.
    """
    if not adjustment:
        return odds_home, odds_draw, odds_away

    def _adjust(value):
        if value is None:
            return None
        return max(_to_decimal(value) + adjustment, ODDS_FLOOR).quantize(
            Decimal('0.01'),
            rounding=ROUND_HALF_UP,
        )

    return _adjust(odds_home), _adjust(odds_draw), _adjust(odds_away)


LAY_SPREAD_FLOOR = Decimal('0.01')


def resolve_effective_lay_spread(match_spread, default_spread) -> Decimal:
    """Returns a match's own lay_spread_override if set, else the global default."""
    return match_spread if match_spread is not None else default_spread


def compute_lay_price(back_odds, spread: Decimal):
    """
    Returns the synthetic house lay price: back_odds plus a flat spread,
    floor-clamped so the result is always strictly greater than back_odds -
    the arbitrage-safety guarantee - regardless of what `spread` value
    reaches this function, even 0 or a negative number (the
    HouseLiquidityConfig/Match validators should already reject those, but
    this is the real, unconditional, code-level guarantee, exactly like
    ODDS_FLOOR above). `None` back_odds passes straight through - the real
    2-way-market guarantee is that a caller never invokes this for the
    draw selection on a match with no draw market at all.
    """
    if back_odds is None:
        return None
    effective_spread = max(_to_decimal(spread), LAY_SPREAD_FLOOR)
    return (_to_decimal(back_odds) + effective_spread).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )


def resolve_user_extra_adjustment(user, match) -> Decimal:
    """
    Most-specific-wins resolution for the per-user odds-override cascade:
    a UserMatchOddsOverride row's own `adjustment` for this exact
    (user, match) pair if set, else this user's own
    odds_adjustment_override (every match), else zero (no extra change -
    the user sees exactly the standard match-level price). `user` may be
    None or anonymous, in which case there is nothing to look up and this
    always returns zero.

    A UserMatchOddsOverride row may exist but have `adjustment=None` (it
    exists only to carry a lay_spread_override instead) - that must fall
    through to the next level exactly like no row existing at all, not be
    treated as an explicit "zero adjustment".
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return Decimal('0')

    from .models import UserMatchOddsOverride

    specific = UserMatchOddsOverride.objects.filter(user=user, match=match).first()
    if specific is not None and specific.adjustment is not None:
        return specific.adjustment
    if user.odds_adjustment_override is not None:
        return user.odds_adjustment_override
    return Decimal('0')


def resolve_user_extra_lay_spread(user, match) -> Optional[Decimal]:
    """
    Most-specific-wins resolution for the per-user lay-spread-reference
    cascade, mirroring resolve_user_extra_adjustment(): a
    UserMatchOddsOverride row's own lay_spread_override for this exact
    (user, match) pair if set, else this user's own lay_spread_override
    (every match), else None (no override - whatever
    resolve_effective_lay_spread() already produced from Match/
    HouseLiquidityConfig stands as-is). `user` may be None or anonymous,
    in which case there is nothing to look up and this always returns
    None.

    Unlike resolve_user_extra_adjustment (a flat delta always ADDED to
    the odds), this returns a REPLACEMENT spread value - lay spread
    replaces, it never composes across levels, exactly like
    Match.lay_spread_override already replaces (not adds to)
    HouseLiquidityConfig.default_lay_spread one level down.

    Display-only: never consulted by
    apps.exchange.services.sync_house_orders_for_match, which stays on
    the 2-level match/global resolve_effective_lay_spread() call - a
    per-user value can never affect the one shared, matched order book.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return None

    from .models import UserMatchOddsOverride

    specific = UserMatchOddsOverride.objects.filter(user=user, match=match).first()
    if specific is not None and specific.lay_spread_override is not None:
        return specific.lay_spread_override
    if user.lay_spread_override is not None:
        return user.lay_spread_override
    return None


def _mode_from_override(override) -> 'ResolvedPricingMode':
    return ResolvedPricingMode(
        back_mode=override.back_mode,
        back_custom_value_home=override.back_custom_value_home,
        back_custom_value_draw=override.back_custom_value_draw,
        back_custom_value_away=override.back_custom_value_away,
        lay_mode=override.lay_mode,
        lay_relative_delta=override.lay_relative_delta,
        lay_custom_value_home=override.lay_custom_value_home,
        lay_custom_value_draw=override.lay_custom_value_draw,
        lay_custom_value_away=override.lay_custom_value_away,
    )


def resolve_match_pricing_mode(match) -> 'ResolvedPricingMode':
    """
    "Custom Odds" mode resolution for the SHARED, public price - the
    match's own PricingOverride row (user is null) if one exists, else
    the site-wide default on HouseLiquidityConfig. This is what feeds the
    real exchange order book (apps.exchange.services.
    sync_house_orders_for_match) - never a per-user value, since a shared
    order book cannot show different users different real tradeable
    prices.
    """
    from .models import HouseLiquidityConfig, PricingOverride

    override = PricingOverride.objects.filter(match=match, user__isnull=True).first()
    if override is not None:
        return _mode_from_override(override)

    config = HouseLiquidityConfig.get_solo()
    return ResolvedPricingMode(
        back_mode=config.default_back_mode,
        back_custom_value_home=config.default_back_custom_value_home,
        back_custom_value_draw=config.default_back_custom_value_draw,
        back_custom_value_away=config.default_back_custom_value_away,
        lay_mode=config.default_lay_mode,
        lay_relative_delta=config.default_lay_relative_delta,
        lay_custom_value_home=config.default_lay_custom_value_home,
        lay_custom_value_draw=config.default_lay_custom_value_draw,
        lay_custom_value_away=config.default_lay_custom_value_away,
    )


def resolve_user_pricing_mode(user, match):
    """
    "Custom Odds" mode resolution for what one specific user sees/is
    charged on the sportsbook only - a user+match row, else a user-only
    row, else None (meaning: no override for this user, whatever
    resolve_match_pricing_mode() already produced stands). Returns None
    for an anonymous/unauthenticated user - there's nothing to look up.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return None

    from .models import PricingOverride

    specific = PricingOverride.objects.filter(user=user, match=match).first()
    if specific is not None:
        return _mode_from_override(specific)

    user_only = PricingOverride.objects.filter(user=user, match__isnull=True).first()
    if user_only is not None:
        return _mode_from_override(user_only)

    return None


def compute_back_and_lay(raw_back_odds, mode: 'ResolvedPricingMode', default_lay_spread: Decimal, selection: str):
    """
    Applies a resolved pricing mode to one raw back-odds value for one
    specific selection ('home'/'draw'/'away'), returning (back, lay).
    back_mode=custom replaces the raw value with THAT selection's own
    custom value outright (no "live price" left to shift - this is why
    the flat odds_adjustment cascade doesn't apply on top of an explicit
    override, see get_effective_odds_for_user) - Home/Draw/Away each get
    their own independent custom number, never one shared value, since a
    match's three outcomes are three different real prices. lay_mode
    picks between the three modes the client asked for; api_lay reuses
    compute_lay_price() completely unchanged; relative applies the same
    shared delta to THIS selection's own back, which already yields a
    different lay per selection without needing its own per-selection
    fields. Regardless of which mode produced it, the result is always
    floor-clamped so lay can never equal or drop below back - the same
    arbitrage-safety guarantee as compute_lay_price's own LAY_SPREAD_FLOOR,
    just applied universally here instead of only to the api_lay case.
    `None` raw_back_odds passes straight through as (None, None) - the
    2-way-market guarantee (no draw price/order for a 2-way match) is
    structural at the caller, exactly like compute_lay_price.
    """
    from .models import BackMode, LayMode

    if raw_back_odds is None:
        return None, None

    back_custom_value = mode.back_custom_value_for(selection)
    if mode.back_mode == BackMode.CUSTOM and back_custom_value is not None:
        back = _to_decimal(back_custom_value)
    else:
        back = _to_decimal(raw_back_odds)

    lay_custom_value = mode.lay_custom_value_for(selection)
    if mode.lay_mode == LayMode.CUSTOM and lay_custom_value is not None:
        lay = _to_decimal(lay_custom_value)
    elif mode.lay_mode == LayMode.RELATIVE and mode.lay_relative_delta is not None:
        lay = (back + _to_decimal(mode.lay_relative_delta)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        lay = compute_lay_price(back, default_lay_spread)

    if lay <= back:
        lay = (back + LAY_SPREAD_FLOOR).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return back, lay


def get_effective_odds_for_user(match, user):
    """
    Returns (odds_home, odds_draw, odds_away) as this specific user should
    see/be charged them. `Match.odds_home/draw/away` (the shared, public
    price every other user sees) is never written to or derived from a
    per-user value - this is computed fresh on every call from whatever
    the match's current standard odds are, so turning off a user's
    override always and automatically falls back to the untouched shared
    price, nothing to clean up. Reuses apply_odds_adjustment() unchanged,
    so the same unconditional 1.01 floor-clamp guarantee applies here too.

    If this user has their own Custom Odds pricing-mode override (most
    specific: user+match, else user-only), it replaces the back value
    entirely for them - most-specific-wins, ahead of the flat delta
    adjustment above, since an explicit override supersedes "shift the
    live price a bit".
    """
    extra = resolve_user_extra_adjustment(user, match)
    if not extra:
        odds_home, odds_draw, odds_away = match.odds_home, match.odds_draw, match.odds_away
    else:
        odds_home, odds_draw, odds_away = apply_odds_adjustment(
            extra, match.odds_home, match.odds_draw, match.odds_away,
        )

    user_mode = resolve_user_pricing_mode(user, match)
    if user_mode is None:
        return odds_home, odds_draw, odds_away

    from .models import HouseLiquidityConfig
    effective_spread = resolve_effective_lay_spread(
        match.lay_spread_override, HouseLiquidityConfig.get_solo().default_lay_spread,
    )
    user_spread = resolve_user_extra_lay_spread(user, match)
    if user_spread is not None:
        effective_spread = user_spread
    back_home, _ = compute_back_and_lay(odds_home, user_mode, effective_spread, 'home')
    back_draw, _ = compute_back_and_lay(odds_draw, user_mode, effective_spread, 'draw')
    back_away, _ = compute_back_and_lay(odds_away, user_mode, effective_spread, 'away')
    return back_home, back_draw, back_away


def get_effective_lay_reference_for_user(match, user):
    """
    Display-only reference lay prices for the sportsbook page (the
    sportsbook has no lay-side betting - this is purely informational,
    mirroring what the exchange's real back/lay boxes already show),
    using this user's own pricing mode if they have one, else the match's.
    Never used to charge a bet.
    """
    odds_home, odds_draw, odds_away = get_effective_odds_for_user(match, user)
    mode = resolve_user_pricing_mode(user, match) or resolve_match_pricing_mode(match)

    from .models import HouseLiquidityConfig
    effective_spread = resolve_effective_lay_spread(
        match.lay_spread_override, HouseLiquidityConfig.get_solo().default_lay_spread,
    )
    user_spread = resolve_user_extra_lay_spread(user, match)
    if user_spread is not None:
        effective_spread = user_spread
    _, lay_home = compute_back_and_lay(odds_home, mode, effective_spread, 'home')
    _, lay_draw = compute_back_and_lay(odds_draw, mode, effective_spread, 'draw')
    _, lay_away = compute_back_and_lay(odds_away, mode, effective_spread, 'away')
    return lay_home, lay_draw, lay_away
