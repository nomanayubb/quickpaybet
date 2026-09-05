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
