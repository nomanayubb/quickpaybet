from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple


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
