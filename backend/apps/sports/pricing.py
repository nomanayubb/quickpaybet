from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple


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
    """
    odds = [_to_decimal(odds_home), _to_decimal(odds_draw), _to_decimal(odds_away)]
    implied = [Decimal('1') / odd for odd in odds]
    return sum(implied) - Decimal('1')


def normalize_odds(
    odds_home,
    odds_draw,
    odds_away,
    margin: Decimal = Decimal('0.05'),
) -> Tuple[Decimal, Decimal, Decimal]:
    """Remove the bookmaker's overround and reapply a target margin.

    Args:
        odds_home: raw decimal odds for home
        odds_draw: raw decimal odds for draw
        odds_away: raw decimal odds for away
        margin: target overround  (0.05 means 5%)

    Returns:
        A tuple (home_odds, draw_odds, away_odds) adjusted to the target margin.
    """
    raw_odds = [
        _to_decimal(odds_home),
        _to_decimal(odds_draw),
        _to_decimal(odds_away),
    ]

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

    return (
        normalized[0],
        normalized[1],
        normalized[2],
    )
