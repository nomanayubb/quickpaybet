# Change / Action Log

This document records every meaningful step taken throughout the project based on the full conversation history.

## 1. Greeting Change (Old Codebase)

- Request: make the greeting more casual.
- Change: sample file `show_greeting.py` was updated to say “Hey” instead of “Hello”.
- Status: this belongs to a previous codebase and is no longer relevant.

---

## 2. Adding `provider_key` to `Sport`

**Files changed:**  
- `backend/apps/sports/models.py`  
- `backend/apps/sports/migrations/0002_sport_provider_key.py`

**Action:**  
- Added `provider_key = models.CharField(max_length=100, blank=True, default='')` to `Sport`.

**Command:**  
- `python manage.py migrate sports`

---

## 3. Making Odds Sync Multi‑Sport Aware

**Files changed:**  
- `backend/apps/sports/providers.py`  
- `backend/apps/sports/management/commands/sync_odds.py`

**Action:**  
- `fetch_matches(sport_key=None)` standardised across providers.  
- `TheyOddsAPIProvider.fetch_matches` now accepts the `sport_key` argument.  
- `sync_odds` now loops over all active `Sport` rows and passes each `provider_key` to `fetch_matches`.

---

## 4. Adding Odds Margin Normalisation

**Files changed:**  
- `backend/apps/sports/pricing.py`  
- `backend/apps/sports/management/commands/sync_odds.py`

**Action:**  
- Added `compute_margin()` and `normalize_odds()`.  
- `sync_odds` now normalises raw odds before saving them to `Match`.

**Helper command:**  
- `python manage.py sync_odds --margin 0.05`

---

## 5. Adding `sync_sports` Command

**Files changed:**  
- `backend/apps/sports/management/commands/sync_sports.py`  
- `backend/apps/sports/providers.py`

**Action:**  
- Added `fetch_sports()` to `BaseOddsProvider`.  
- Implemented it in both `MockOddsProvider` and `TheyOddsAPIProvider`.  
- Added a command that imports available sports from The Odds API.

**Command:**  
- `python manage.py sync_sports`

---

## 6. Switching Development Database to SQLite

**Files changed:**  
- `backend/config/settings.py`

**Action:**  
- Changed default `DATABASE_URL` to `sqlite:///db.sqlite3`.  
- This lets the project run without Docker/PostgreSQL locally.

**Command:**  
- `python manage.py migrate`

---

## 7. Supporting Two‑Outcome Markets in Pricing

**Files changed:**  
- `backend/apps/sports/pricing.py`

**Action:**  
- Updated `normalize_odds()` so it accepts `odds_draw = None`.  
- For markets without a draw, the return tuple uses `None` in the draw position.

---

## 8. Handling 404 and 422 Odds API Errors

**Files changed:**  
- `backend/apps/sports/providers.py`

**Action:**  
- `_raw_get()` now returns an empty list when The Odds API responds with 404 or 422.  
- This prevents `sync_odds` / `sync_results` from crashing when a sport temporarily has no data.

---

## 9. Adding Score Fetching

**Files changed:**  
- `backend/apps/sports/providers.py`

**Action:**  
- Added `fetch_scores()` to provider classes.  
- `TheyOddsAPIProvider.fetch_scores()` calls the `/scores/?daysFrom=1` endpoint.  
- It converts scores into a normalized list.

---

## 10. Adding `sync_results` Command

**Files changed:**  
- `backend/apps/sports/management/commands/sync_results.py`

**Action:**  
- Command pulls results from The Odds API for each active sport.  
- For each completed match it:
  1. Finds the local `Match`
  2. Sets status to `FINISHED`
  3. Saves home/away scores
  4. Calls `settle_bets_for_match(match)`

**Command:**  
- `python manage.py sync_results`

---

## 11. Confirming Bet Settlement Logic

**Files changed (verified, not modified):**  
- `backend/apps/bets/services.py`

**Action:**  
- Verified that `settle_bets_for_match()` exists and is imported inside `sync_results.py`.  
- This connects result import with automatic bet and parlay settlement.

---

## 12. Skipping Invalid Provider Keys

**Files changed:**  
- `backend/apps/sports/providers.py`  
- `backend/apps/sports/management/commands/sync_sports.py`  
- `backend/apps/sports/management/commands/sync_odds.py`  
- `backend/apps/sports/management/commands/sync_results.py`

**Action:**  
- Defined `INVALID_PROVIDER_KEYS = {'football'}` in `providers.py`.  
- All sync commands now skip this invalid key, avoiding “HTTP Error 404: Not Found”.

---

## 13. Adding `--sport` Option

**Files changed:**  
- `backend/apps/sports/management/commands/sync_odds.py`  
- `backend/apps/sports/management/commands/sync_results.py`

**Action:**  
- Added `--sport <provider_key>` argument to filter sync to a single sport.  

**Example:**  
- `python manage.py sync_odds --sport soccer_epl`  
- `python manage.py sync_results --sport soccer_epl`

---

## 14. Commands To Run Next

Prepare environment:

- `$env:ODDS_PROVIDER = "theoddsapi"`
- `$env:ODDS_API_KEY = "245cab3c5cff52364fa847df4171e64f"`

Run in order:

1. `python manage.py migrate`
2. `python manage.py sync_sports`
3. `python manage.py sync_odds --sport soccer_epl`
4. `python manage.py sync_results --sport soccer_epl`

Check counts:

- `python manage.py shell -c "from apps.sports.models import Sport, Match; print(Sport.objects.count(), Match.objects.count())"`

---

## 15. Git Commits Made During This Session

| Commit Hash | Message |
|-------------|---------|
| `840e4e8` | feat: add provider_key field to Sport model |
| `cc3ce7b` | feat: support multi-sport odds sync via provider_key |
| `2391e8d` | feat: support per-sport key in odds providers |
| `4ada65a` | docs: add odds sync setup instructions |
| `b17a0af` | feat: add sync_sports command and provider support |
| `fdcfb40` | chore: add backend command snippet files |
| `e548d31` | feat: add provider_key field to Sport model |
| `9c5806f` | feat: add odds margin normalization to sync_odds |
| `351f029` | fix: handle two-outcome markets in pricing |
| `03297ed` | fix: handle 422 odds errors and draw-less H2H markets |
| `a2c7006` | feat: sync match results from odds provider and settle bets |
| `761eb98` | feat: add fetch_scores support to odds providers |
| `6c3e73b` | fix: default database to SQLite for local development |
| `a2ccc2d` | fix: skip invalid sport keys and support per-sport sync |

*End of change log.*
