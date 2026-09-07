from datetime import timedelta
from decimal import InvalidOperation, Decimal

from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Match, OddsAdjustmentConfig, OddsHistoryEntry, RealtimeOddsConfig, Sport
from .pricing import apply_odds_adjustment, normalize_odds, resolve_effective_adjustment
from .providers import get_odds_provider

DEFAULT_MARGIN = Decimal('0.05')


def _viewer_cache_key(match_id: int) -> str:
    return f'match_viewers:{match_id}'


def record_viewer_heartbeat(match: Match, viewer_key: str, stale_after_seconds: int) -> int:
    """
    Marks `viewer_key` as currently viewing `match` and prunes any viewer
    not seen within `stale_after_seconds` (closed tab / navigated away),
    returning the resulting viewer count. Built entirely on Django's
    existing cache framework (Redis in production, locmem locally) - no
    new infrastructure, no separate presence system.
    """
    key = _viewer_cache_key(match.id)
    now = timezone.now().timestamp()
    viewers = cache.get(key) or {}
    viewers[viewer_key] = now
    cutoff = now - stale_after_seconds
    viewers = {k: v for k, v in viewers.items() if v >= cutoff}
    cache.set(key, viewers, timeout=3600)
    return len(viewers)


def get_effective_refresh_interval(match: Match, config: RealtimeOddsConfig):
    """
    Returns the refresh interval (seconds) that applies to `match` right
    now, or None if it shouldn't refresh in real time at all (already
    finished/cancelled - nothing left to refresh).
    """
    if match.status == Match.Status.LIVE:
        return config.live_refresh_seconds
    if match.status == Match.Status.SCHEDULED:
        proximity_window = timedelta(minutes=config.proximity_window_minutes)
        if match.start_time - timezone.now() <= proximity_window:
            return config.proximity_boost_seconds
        return config.not_started_refresh_seconds
    return None


def _refresh_lock_key(sport_id: int) -> str:
    return f'odds_refresh_lock:{sport_id}'


def maybe_refresh_sport_odds(sport: Sport, interval_seconds: int) -> bool:
    """
    Ensures at most one real provider fetch happens per `interval_seconds`
    window for this sport, no matter how many viewers' poll requests arrive
    concurrently within that window - cache.add() is atomic and only
    succeeds for the first caller. Everyone else within the same window
    reads whatever is already in the database (updated by the caller that
    won the race, or from an earlier window) instead of triggering a
    second real call. Returns True if this call actually performed a real
    fetch, False otherwise (someone else already handled this window, the
    sport has no provider_key, or the fetch failed).

    One call refreshes every match for this sport at once - the provider's
    odds endpoint returns a whole sport's matches per call, not one at a
    time, so this is also the most credit-efficient shape, not just the
    simplest one.

    Match.odds_home/draw/away are always overwritten in place (the "only
    keep the latest value" behavior, on by default, costs nothing extra in
    storage). A full per-refresh history (OddsHistoryEntry) is additionally
    written only when RealtimeOddsConfig.store_full_odds_history is on -
    off by default, so this feature costs nothing until an admin
    deliberately opts into it.
    """
    if not sport.provider_key:
        return False

    if not cache.add(_refresh_lock_key(sport.id), True, timeout=interval_seconds):
        return False

    provider = get_odds_provider()
    try:
        items = provider.fetch_matches(sport_key=sport.provider_key)
    except Exception:
        return False

    # Only read once per call, not once per match, and never at all unless
    # a fetch is actually happening - keeping this out of the hot path
    # when there's nothing to refresh.
    keep_history = RealtimeOddsConfig.get_solo().store_full_odds_history
    default_adjustment = OddsAdjustmentConfig.get_solo().default_adjustment
    history_entries = []

    for item in items:
        start_time = parse_datetime(item['start_time'])
        if not start_time:
            continue
        try:
            odds_home, odds_draw, odds_away = normalize_odds(
                item['odds_home'], item['odds_draw'], item['odds_away'], margin=DEFAULT_MARGIN,
            )
        except (ValueError, ZeroDivisionError, InvalidOperation):
            continue

        matches = list(Match.objects.filter(
            sport=sport,
            home_team=item['home_team'],
            away_team=item['away_team'],
            start_time=start_time,
            status__in=[Match.Status.SCHEDULED, Match.Status.LIVE],
        ))
        if not matches:
            continue

        # Applied per-match (not bulk) since each match may carry its own
        # odds_adjustment override, which can differ across matches that
        # otherwise share this same provider quote.
        for m in matches:
            effective_adjustment = resolve_effective_adjustment(m.odds_adjustment, default_adjustment)
            adj_home, adj_draw, adj_away = apply_odds_adjustment(
                effective_adjustment, odds_home, odds_draw, odds_away,
            )
            Match.objects.filter(pk=m.pk).update(
                odds_home=adj_home, odds_draw=adj_draw, odds_away=adj_away,
            )
            m.odds_home, m.odds_draw, m.odds_away = adj_home, adj_draw, adj_away
            if keep_history:
                history_entries.append(
                    OddsHistoryEntry(match=m, odds_home=adj_home, odds_draw=adj_draw, odds_away=adj_away)
                )

            from apps.exchange.services import sync_house_lay_orders_for_match
            sync_house_lay_orders_for_match(m)

    if history_entries:
        OddsHistoryEntry.objects.bulk_create(history_entries)

    return True
