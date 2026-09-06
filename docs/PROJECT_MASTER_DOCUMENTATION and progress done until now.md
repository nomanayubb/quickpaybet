# QuickPayBet — Project Master Documentation

**Purpose of this file:** this is the single source of truth for the QuickPayBet project. Any developer or AI assistant picking up this project should read this file first, before touching any code. Only dig into `.aider.chat.history.md` (a 3.2 MB raw chat log from the original build sessions with DeepSeek/aider) if you need an exact original quote — it is not required reading and is error-prone to parse.

**How to keep this file updated (rule for every future session, human or AI):** update this file when you finish meaningful work — a new capability, an architectural decision, a phase completed, a real gap discovered. Do **not** log every individual bug fix, error message, or minor edit here — that belongs in commit messages, not here. This file answers three questions: *what path has the project traveled so far, where does it actually stand right now, and what is the goal it's heading toward.* Keep it narrative and honest, not a changelog.

Last compiled: 2026-09-06, by Claude Code, after a full line-by-line audit of the actual codebase (not just the chat history) plus live reproduction of bugs by running the code.

---

## 0. Read this first — the path, where we are, and where we're going

**The path so far:** the client (goal.txt) asked, in a long free-form brief, for a professional, fully-automated sports betting platform in Python/Django: real-time odds a user can bet on, automatic settlement, automatic crypto payment verification, a hierarchical admin/master/agent/user system with configurable limits and affiliate commissions, strict security around odds/money, and a codebase modular enough to bolt on new payment providers, odds providers, and even casino games later without rewrites. Budget was tight — free/cheap tools only.

That brief was handed to DeepSeek (via `aider`) over roughly 13 chat sessions on 2026-09-05, which produced 73 git commits: a Django project with 9 apps, a working wallet ledger, a real (free-tier) odds provider integration, single and parlay betting, an affiliate commission system, a server-rendered web UI, and Docker/Nginx deployment configs. That work is real and mostly well-structured — it is not a toy. A previous documentation pass (also on 2026-09-05) read all 73 commits and the chat log and produced an accurate architectural snapshot of what had been *built*.

**Where we actually stand (as of 2026-09-06, verified by running the code, not just reading it):** that architectural snapshot was correct about what was *built*, but nobody had actually run the test suite or exercised the live app end-to-end. Doing that surfaced several foundation-level problems that don't show up by reading code alone: a live financial exploit (users could credit their own wallet with unlimited fake money), withdrawals that silently didn't pay out, a privilege-escalation hole in user management, and the site returning HTTP errors instead of running at all without a manually-created `.env` file. None of this was malicious — it reads as leftover developer/testing shortcuts that were never removed before being treated as "done" — but it means **the project was not actually at a safe, working foundation**, despite looking complete from the outside (working URLs, passing-looking feature list).

**Current position:** the foundation-hardening pass described above is complete. Every item found — the exploit, the fake withdrawals, the privilege escalation, the broken default configuration, an entire admin section that turned out to be unreachable (found only because it was actually clicked through, not just read), and several smaller correctness bugs — is fixed and re-verified live (not just by reading the diff). The full Django test suite (18 tests) passes. The codebase is now in a genuinely working, safe-to-build-on state. Nothing has been committed to git yet — that's a deliberate choice, not an oversight (see Section 11, rule 11) — review the working tree and commit when ready. From here, work moves from "fix what's broken" to "build what's next" (Section 9).

**The goal:** a genuinely production-ready, professional betting website matching the full scope of `docs/goal.txt` — real money can move in and out safely, odds and settlement are tamper-proof and automatic, the admin hierarchy is properly access-controlled, and the codebase stays modular enough to extend (new payment/odds providers, casino games) without rewrites. Everything short of that goal is tracked either in Section 8/9 (technical work) or `client tasks.md` (things only the client can decide/provide, like real API credentials, domain, legal text).

**Rule for continuing this project (any session, human or AI):** verify before trusting. This project's own history shows that a plausible-sounding, well-organized codebase and a confident-sounding documentation file can both still be wrong about whether things actually work. Run the tests. Reproduce the bug. Read the actual file. Then write it down here.

---

## 1. What this project is (in plain terms)

**QuickPayBet** is a sports betting website being built for a client. The client (communicating mostly in Urdu/English mixed, see `docs/goal.txt` for the original brief verbatim) asked for:

- A working betting site with **accurate odds math**, not a toy.
- Full **admin panels**, extensible later (e.g. adjustable per-user/per-master bet limits, ability to turn affiliate payouts on/off, control who can withdraw as a "master" account or not).
- A **fully automatic workflow**: real-time odds come in → user places a bet → when the match finishes, the system automatically checks the result → automatically credits or debits the user's balance → automatically verifies crypto payments — with **no manual work** required day-to-day.
- Odds should come from a real provider by default, but **admin must be able to override/adjust them** manually from the admin panel (e.g. add or reduce margin).
- **Crypto payments** first, but built so other payment methods/countries can be added later without a rewrite.
- Everything **highly modular and flexible** — new features (casino games, new payment providers, new odds sources) must be addable later without breaking what exists.
- **Strong security** around odds/rates so nothing can be hacked, cheated, or bypassed.
- Budget-conscious: free/cheap technology choices only.
- The client explicitly told the AI coder to **proceed and complete tasks without stopping to ask permission each time**, to save on token/API costs.

---

## 2. Technology stack (verified from actual code)

| Layer | Technology | Verified from |
|---|---|---|
| Backend language/framework | Python + **Django 5.2** | `backend/requirements.txt` |
| API | **Django REST Framework 3.15.2** | `requirements.txt` |
| Auth | **djangorestframework-simplejwt 5.3.1** (JWT) + Django session auth for the web UI | `requirements.txt`, `apps/accounts` |
| Database (dev default) | **SQLite** (`db.sqlite3`, gitignored, local only) | `config/settings.py` (`DATABASE_URL` default `sqlite:///db.sqlite3`) |
| Database (production-capable) | **PostgreSQL**, via `psycopg2-binary 2.9.9` and `DATABASE_URL` | `requirements.txt`, `docker-compose.yml` |
| Cache / Celery broker | **Redis 5.0.8**, via `REDIS_URL` | `requirements.txt`, `settings.py` |
| Background tasks | **Celery 5.4.0**, with a Beat schedule already defined in `config/celery.py` | `config/celery.py`, `apps/bets/tasks.py`, `apps/sports/tasks.py` |
| CORS | `django-cors-headers 4.4.0` | `requirements.txt` |
| Env/config loading | `django-environ 0.11.2` (`.env` file, gitignored — **must exist locally, see Section 6**) | `settings.py`, `.env.example` |
| WSGI server (prod) | `gunicorn 22.0.0` | `requirements.txt` |
| Web UI | Server-rendered **Django templates** (no separate frontend framework) | `backend/templates/web/*.html` |
| Odds data provider | **The Odds API** (real, free-tier key), with a `mock` provider as fallback/default | `apps/sports/providers.py`, `.env.example` |
| Crypto payments | Adapter pattern: `mock` provider (default) and a **NOWPayments** provider — **deposits are real and live-tested** (2026-09-06, with the client's real API key); withdrawals/payouts are coded but blocked on a separate credential (see Section 8) | `apps/payments/providers.py`, `.env.example` |
| Containerization | Docker + `docker-compose.yml` (dev) and `docker-compose.prod.yml` (prod, with `nginx`) | repo root |
| Reverse proxy (prod) | **Nginx**, `nginx/nginx.conf` (proxies to `backend:8000`, serves `/static/`) — **no TLS/443 block configured yet**, see Section 8 | `nginx/nginx.conf` |

All choices are free/open-source or have a free tier, per the client's budget constraint.

---

## 3. Repository layout

```
betting project/
├── backend/                  ← the entire Django application (the real product)
│   ├── config/                ← Django project settings, root URLs, Celery app, WSGI/ASGI
│   ├── apps/                  ← one Django app per business domain (see Section 4)
│   ├── templates/web/         ← all HTML pages (user-facing + admin custom pages)
│   ├── manage.py, requirements.txt, requirements-dev.txt
│   ├── Dockerfile / Dockerfile.prod
│   ├── .env.example           ← full list of every environment variable the app understands
│   ├── .env                   ← gitignored, LOCAL ONLY. Must exist for the app to behave correctly (see Section 6).
│   └── db.sqlite3             ← gitignored, local dev database file
├── docs/                      ← project documentation (this file lives here)
├── nginx/nginx.conf           ← production reverse-proxy config
├── docker-compose.yml         ← local dev stack (Postgres + Redis + Django, hardcodes DEBUG=True)
├── docker-compose.prod.yml    ← production stack (adds nginx, gunicorn, reads real .env)
├── README.md                  ← feature status table, quick start
├── client tasks.md            ← tracked list of decisions/inputs ONLY the client can provide
└── .aider.chat.history.md     ← raw historical chat log (do not re-read unless necessary)
```

---

## 4. Every Django app: purpose and files

### 4.1 `apps/accounts` — users, roles, auth
Custom `User` model (extends `AbstractUser`): `email` (unique, login field), `role` (`user`/`agent`/`master`/`admin`), `parent` (self-FK — builds the affiliate hierarchy), `commission_rate`, `min_bet_amount`, `max_bet_amount`, `is_betting_enabled`. `PasswordResetToken` model. JWT register/login/refresh, "me" endpoint, password reset (email-based), admin endpoint to edit a user's role/limits/commission — **this last endpoint had a privilege-escalation hole, fixed this session; see Section 8.**

### 4.2 `apps/wallet` — balances and transaction ledger
`Wallet` (balance, reserved_balance) and `WalletTransaction` (ledger row). `services.py` has `deposit_funds()`/`withdraw_funds()` — the only functions allowed to change a balance, both atomic with row locking. This core money arithmetic is solid and was not part of the problems found. A `Wallet` is auto-created via signal when a `User` is created.

### 4.3 `apps/sports` — sports, tournaments, matches, odds pipeline
`Sport`/`Tournament`/`Match` models. `providers.py` is the odds-provider adapter: `BaseOddsProvider` interface, `MockOddsProvider` (default), `TheyOddsAPIProvider` (real integration with The Odds API). `pricing.py` applies house margin and handles two-outcome (no-draw) markets. Management commands: `sync_sports`, `sync_odds` (pulls matches/odds, supports `--sport`/`--margin`), `sync_results` (pulls finished scores, settles bets, supports `--sport`). A Celery task auto-flips `Match.status` to `LIVE` once `start_time` passes.

### 4.4 `apps/bets` — the betting engine
`Bet` (single), `ParlayBet` + `ParlayLeg` (multi-selection). All business logic lives in `services.py`, not views: `place_bet()`, `place_parlay_bet()`, `settle_bet()`/`settle_bets_for_match()`, `refund_bet()`, parlay settlement helpers, and affiliate commission crediting on losing bets. Money math here is correct and atomic.

### 4.5 `apps/payments` — crypto deposits/withdrawals
`CryptoPayment` model. `providers.py`: `BasePaymentProvider` interface, `MockPaymentProvider` (default), `NOWPaymentsProvider` (real API calls + HMAC webhook verification). `services.py` creates a pending `CryptoPayment` and, on a **provider-verified webhook**, credits the wallet; `create_withdrawal_request()` deducts+ledgers atomically, calls the provider, and auto-refunds if the provider rejects the payout — money can never be silently lost or fake-credited. **Withdrawals did not actually call the provider at all before this session — fixed; see Section 8.**

**Live-tested against the real NOWPayments account (2026-09-06):** deposit creation works end-to-end (confirmed: a real deposit address was generated). The IPN secret was also provided and tested — and testing it caught a real bug: `verify_webhook()` was hashing the raw request body directly, but NOWPayments actually signs the JSON **re-serialized with recursively sorted keys** (`json.dumps(data, separators=(',', ':'), sort_keys=True)`) before hashing. The old code would have silently rejected essentially every real webhook, meaning deposits would never have auto-confirmed once a real provider went live. Fixed and confirmed correct with a realistic simulated webhook (accepts a correctly-signed payload, rejects a tampered one). Payouts (withdrawals) correctly and safely fail right now — NOWPayments requires a JWT token from a separate email+password+2FA login for the `/payout` endpoint, which this codebase doesn't implement yet (the API key alone, which works for deposits, is rejected for payouts with `AUTH_REQUIRED`). Confirmed the failure path is safe: the user's wallet is fully refunded and the payment is marked `failed`, not silently lost. See `client tasks.md` for what's needed to finish this (the client's NOWPayments login + 2FA, or the client processes payouts manually from their own NOWPayments dashboard for now).

### 4.6 `apps/reports` — admin/master analytics
Overview report, daily report, per-sport/per-user/per-match breakdowns.

### 4.7 `apps/audit` — audit trail
`AuditLog` model and a `create_audit_log()` helper existed but **were never called from anywhere** — the audit log was always empty. Fixed this session to actually record key admin/money actions; see Section 8.

### 4.8 `apps/common`
`seed_data` management command for dev data. `tests.py` — full-flow API tests (register → deposit → bet → settle).

### 4.9 `apps/web` — the actual website (server-rendered)
Every page a browser hits: home, login, register, dashboard, matches, bet slip, parlay, wallet, bet history, password reset, and the full admin section. This is where the exploit, the crash-on-validation-error bug, and the privilege escalation lived (see Section 8) — the underlying services were fine, the views wrapping them had the bugs.

The custom admin section (manage users/sports/matches, reports, audit log) lives under **`/panel/...`**, not `/admin/...`. `/admin/` is reserved for Django's own built-in admin — it registers a catch-all for anything under that prefix, so a URL under `/admin/...` never reaches a custom view at all. This project shipped with the custom admin section mistakenly placed at `/admin/...` for a while, which made it completely unreachable (silently intercepted, redirecting to Django's own login or 404ing) — found only by actually clicking through the site, not by reading the code. Any new admin-only page belongs under `/panel/`, never `/admin/`.

### 4.10 `config/` (Django project, not an app)
`settings.py` — all config from environment variables via `django-environ`. `urls.py` — root routing (`/admin/`, `/api/auth/`, `/api/`, `/api/payments/`, `/` for the web app). `celery.py` — Celery app + Beat schedule (already defined, see Section 5).

---

## 5. Core workflow: how a bet actually settles

1. **Odds come in**: `sync_sports` (once) → `sync_odds` pulls matches/odds from the provider, applies house margin, saves `Match` rows. Scheduled via Celery Beat every 6 hours (`config/celery.py`).
2. **A match goes live automatically**: a Celery task flips `Match.status` to `LIVE` once `start_time` passes. Scheduled every minute.
3. **User places a bet**: `place_bet()`/`place_parlay_bet()` reserve the stake from the wallet and create a `Bet`/`ParlayBet` — one atomic DB transaction.
4. **Result comes in**: `sync_results` pulls finished scores, marks the match `FINISHED`, and calls `settle_bets_for_match()`. Scheduled via Celery Beat every 30 minutes — **this task was a stub that didn't actually run the command; fixed this session, see Section 8.**
5. **Settlement happens automatically**: every pending `Bet`/`ParlayLeg` is marked `WON`/`LOST`, wallets credited, and affiliate commission credited to the referrer on a loss.
6. **Everything can also be triggered manually** via Django admin actions (settle/cancel a match, edit odds/limits/commission) for admin override.

**Running the scheduler in practice:** the Beat schedule only fires if a `celery beat` process is actually running alongside a `celery worker` process. Neither `docker-compose.yml` nor `docker-compose.prod.yml` currently run a beat/worker service — only `backend` (the Django web process) is defined. This is a real remaining gap; see Section 8.

---

## 6. Environment variables

From `backend/.env.example` (authoritative list of what the app understands):

```
DEBUG, SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, REDIS_URL, CORS_ALLOW_ALL_ORIGINS
EMAIL_BACKEND, EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS, DEFAULT_FROM_EMAIL

ODDS_PROVIDER=mock            # mock | theoddsapi
ODDS_API_KEY=
ODDS_API_URL=
ODDS_SPORT_KEY=soccer_epl

PAYMENT_PROVIDER=mock         # mock | nowpayments
NOWPAYMENTS_API_KEY=
NOWPAYMENTS_API_URL=https://api.nowpayments.io/v1
NOWPAYMENTS_IPN_SECRET=
NOWPAYMENTS_IPN_CALLBACK_URL=

SECURE_SSL_REDIRECT=          # added this session — see Section 8, item on broken default config
CACHE_BACKEND=redis           # 'redis' (default, matches Docker) or 'locmem' for a machine with no local Redis server
```

**Critical operational fact (this is why Section 8 has a whole item about it):** `backend/.env` does not exist anywhere in this repo (correctly gitignored) and never has. Anyone running the project outside `docker-compose.yml` (which hardcodes `DEBUG=True` in its own config) — i.e. anyone running `manage.py runserver` or the test suite directly — gets Django's safe-but-unconfigured defaults, which previously caused the whole site to force HTTPS redirects on plain HTTP and break entirely. **A real local `.env` now exists on this machine** (gitignored, not in git) — anyone else setting up this project fresh must copy `.env.example` to `.env` and fill in real values before running anything outside Docker.

**Security note:** an earlier session typed a real Odds API key directly into a terminal and it ended up committed in plaintext inside `docs/CHANGE_LOG.md`. That has been removed from the file this session, but it was exposed in git history — **rotate that key with the provider** before this project goes anywhere public. Never put real secrets in any file other than the gitignored `.env`.

---

## 7. Chronological history

**Phase A — throwaway/setup (2026-09-05, 04:08–04:20):** trivial test commits, no functional value.

**Phase B — foundation (2026-09-05, 06:30–07:04):** Django project + all core apps created; Docker Compose; wallet/sports/bets models and APIs; user hierarchy and betting limits; dev seed data; payments app with webhook-driven deposits; audit log app (scaffolded, not yet wired to anything); Celery auto-settlement task; password reset; odds provider sync + live-match automation.

**Phase C — web UI + hardening (2026-09-05, 07:08–08:50):** full server-rendered website; payment provider adapter (mock + NOWPayments skeleton); unit + full-flow tests; admin dashboard/reports/pages; **a dev-only deposit-confirmation shortcut was added for local testing convenience and never removed** (this became a critical security hole, see Section 8); email password reset + rate limiting + audit page + deployment config; parlay betting; affiliate commissions; pagination, webhook signature validation; `client tasks.md` created.

**Phase D — real Odds API integration (2026-09-05, 09:15–10:33):** real free-tier "The Odds API" wired in after rejecting paid options (a $90/month feed, Betfair's business-verification requirement); `provider_key` on `Sport`; multi-sport `sync_odds`; margin normalization; `sync_sports`; two-outcome market support; tolerance for provider 404/422 responses; `sync_results` added; SQLite made the local default so the project runs without Docker/Postgres.

**Phase E — documentation attempts (2026-09-05, 10:33–11:47):** several earlier attempts to write a master history document failed or were left empty (`docs/COMPLETE_HISTORY.md`, an earlier `docs/PROJECT_MASTER_DOCUMENTATION.md`); a successful version was produced late on 2026-09-05 by reading the full chat log + git log + live code together — but without actually *running* the code.

**Phase F — foundation audit and hardening (2026-09-06, Claude Code):** ran the actual test suite and reproduced behavior live for the first time in this project's history (rather than reading code and inferring), then fixed everything found. Fixed: a live wallet self-funding exploit, non-functional withdrawals, a privilege-escalation hole in user role management, a broken default configuration that made the site unusable without a manually-created `.env`, a web-layer exception-handling bug that crashed bet placement on any validation error, a Celery Beat task that silently did nothing, an audit log that was never actually written to, an entire custom admin section that was silently unreachable due to a URL prefix collision with Django's built-in admin, and a pre-existing model/migration drift. Also cleaned up ~25 confirmed-junk files and the git index (no commits made — that stays the client's/developer's call). See Section 8 for the full list. All 18 tests pass.

---

## 8. Current known state — fixed vs. still open

*(This section is the living "what's actually true right now" list. Update it, don't let it accumulate stale entries — when something here is fixed, rewrite the line to say so plainly; don't leave a trail of every intermediate attempt.)*

### Fixed this session (2026-09-06)
- Wallet self-funding exploit (`/wallet/confirm-deposit/<id>/` letting any user confirm their own deposit).
- Withdrawals silently not paying out (no provider call, no `CryptoPayment` record).
- Privilege escalation in user role/hierarchy management (API + web admin).
- Broken default configuration (site unusable without a manually-created `.env`; `SECURE_SSL_REDIRECT` is now its own explicit env var instead of being silently tied to `DEBUG`).
- Web views crashing with HTTP 500 on any betting validation error (wrong exception type caught).
- `sync_results_celery` Beat task being a no-op stub.
- Audit log never being written to.
- `select_for_update()` queries evaluated outside an explicit `atomic()` block in settlement code (worked on SQLite by accident, was not guaranteed safe on Postgres).
- Duplicate `IsAdminOrMaster` permission class defined separately in four apps.
- Leaked API key removed from `docs/CHANGE_LOG.md` (rotation with the provider is still the client's/developer's action to take).
- **The entire custom admin section (`/admin/users/`, `/admin/matches/`, `/admin/sports/`, `/admin/reports/`, `/admin/audit/`) was completely unreachable** — silently intercepted by Django's own built-in admin, which claims the whole `admin/` URL prefix. Moved to `/panel/...`. This was the single most consequential bug found, and it was found only by actually clicking through the site, not by reading code — every other bug in this list was caught by reading + running unit tests; this one wasn't visible either way until someone tried the real page.
- Pre-existing model/migration drift on `WalletTransaction.txn_type` (`makemigrations --check` now clean project-wide).
- Repo hygiene: deleted ~25 confirmed-junk files (stray command/instruction text saved as filenames, a duplicate draft doc in an accidentally-created "New folder"), cleaned up the git index so leftover never-committed staged entries don't linger, removed a stale `.git/index.lock`.

All 18 tests in the Django test suite pass. Every critical item above was reproduced live before fixing and re-verified live after (not just confirmed by reading the diff) — see `FOUNDATION_FIXES.md` for exact reproduction notes if ever needed, though that file is not meant to be maintained going forward.

### Additional hardening (2026-09-06, same day, after the foundation pass)
- **API login had no brute-force protection** — only the web login did (a 5-attempts/60s lockout). The API login (`/api/auth/login/`) was only covered by the generic 100/hour anonymous rate, which is far too loose for password guessing. Added a dedicated `auth` throttle scope (10/hour) applied to API login, registration, and password-reset-request; confirmed live (10 attempts allowed, then HTTP 429). Also added a per-IP rate limit to the web registration and password-reset-request views, which had none at all before.
- **Withdrawal API had no dedicated rate limit** — added a `withdraw` throttle scope (10/hour) as defense-in-depth against a compromised session spamming withdrawal requests.
- **Odds override endpoint accepted negative or zero odds with no validation at all** — confirmed live: an admin/master could set `odds_home` to `-5.00` or `0` via `PATCH /api/matches/<id>/update-odds/`, which would corrupt settlement math (a bet against zero odds pays out zero; negative odds are nonsensical). Fixed in both the API (`MatchOddsUpdateSerializer`) and the web admin match forms (`apps/web/views.py`) — decimal odds must now be greater than 1.00 everywhere they can be set by a human. (Provider-synced odds via `sync_odds`/`pricing.normalize_odds()` were already safe by construction — this only affected manual overrides.)
- **CORS reviewed, not changed**: `CORS_ALLOW_ALL_ORIGINS=True` is permissive but lower-risk than it looks — `CORS_ALLOW_CREDENTIALS` is not set (defaults to `False`), so cross-origin requests can't carry cookies/session auth; JWT bearer tokens aren't automatically attached by a browser to a request a malicious site initiates. Tightening this to a specific allowed origin is still worth doing before public launch, but is genuinely blocked on the client's domain/frontend decision (`client tasks.md`) — there's no real origin to restrict it to yet.

*(Exact diffs are in git history/commit messages once committed — nothing has been committed yet; that's deliberate, see Section 11 rule 11 — this file records that it's done and roughly what "done" means, not the mechanics of the fix.)*

### Still genuinely open
- **Real crypto deposits work end-to-end (live-tested 2026-09-06, including webhook signature verification); real crypto withdrawals don't yet.** `PAYMENT_PROVIDER` still defaults to `mock` for local dev. Withdrawals need the client to provide their NOWPayments account email + password + 2FA method (a separate credential from the API key — tracked precisely in `client tasks.md`) so the `/payout` endpoint's JWT auth can be implemented; until then, withdrawal attempts safely fail and auto-refund rather than losing money.
- **No `celery beat`/`celery worker` service is defined in `docker-compose.yml` or `docker-compose.prod.yml`.** The schedule exists in code but nothing runs it unless someone starts those processes manually.
- **The Odds API free tier doesn't return data for most league keys** — confirmed plan/API limitation, not a code defect. Verify which `provider_key`s actually return data before promising specific leagues to end users.
- **No separate frontend** (React/Next/Vue) — current UI is server-rendered Django templates. Works, but not required to change unless the client asks.
- **No CI pipeline** — tests exist and pass (after this session's fixes) but must be run manually.
- **No TLS/443 configured in `nginx/nginx.conf`** — production as currently configured has no real HTTPS termination path yet; blocked on the client's domain/hosting decision (`client tasks.md`).
- **No production deployment has happened.** Configs look ready but are unverified against a real server.

---

## 9. Full phased roadmap

**Phase 0 — Documentation & Approval** — ✅ Done.

**Phase 0.5 — Foundation Hardening** — ✅ Done this session (Section 8's "fixed" list). This phase didn't exist in earlier roadmaps because the problems it fixes weren't known until the code was actually run.

**Phase 1 — Repository & Backend Foundation** — ✅ Done.

**Phase 2 — Authentication & Permissions** — ✅ Done, including the hierarchy-scoping fix from Phase 0.5.

**Phase 3 — Sports / Matches / Odds** — ✅ Mostly done. ⏳ Open: confirm which league keys return real data on the current API plan; get the scheduler actually running in Docker (Phase 0.5 fixed the stub task, but nothing starts `celery beat` yet).

**Phase 4 — Wallet & Crypto Payments** — 🟡 Partly done, significantly more so after 2026-09-06. Ledger + mock flow work end-to-end and are honest about what they do (Phase 0.5). Real NOWPayments **deposits are fully live-tested and working end-to-end, including webhook signature verification** (a real bug in the signature algorithm was found and fixed while testing this — see Section 4.5). ⏳ Open: the client's NOWPayments account email/password/2FA for real payouts (a separate auth flow from the API key, tracked in `client tasks.md`) — until then withdrawals safely fail and auto-refund rather than losing money.

**Phase 5 — Betting Engine** — ✅ Mostly done. ⏳ Open: cash-out and any bet types beyond single/parlay.

**Phase 6 — Settlement Automation** — ✅ Mostly done, more honestly so after Phase 0.5 (the scheduling stub is fixed). ⏳ Open: an actual running `celery beat` + `celery worker` process in the deployment stack.

**Phase 7 — Admin Panel Custom UI** — ✅ Mostly done, now with correct hierarchy-based access control (Phase 0.5).

**Phase 8 — Reports & Analytics** — 🟡 Partly done. ⏳ Open: drill-down by sport/user/match beyond what exists, payout/profit trend views.

**Phase 9 — Security & Hardening** — 🟡 Improved significantly this session (Phase 0.5, plus a follow-up hardening pass same day): brute-force protection now on API login/register/password-reset (10/hour) in addition to the web login's existing lockout; a dedicated `withdraw` throttle scope; odds-override endpoints now reject non-positive/nonsensical odds (a real gap that existed with zero validation before). ⏳ Open: CORS tightened to a real origin once the client picks a domain, a broader input-validation audit beyond what's been checked so far, an OWASP-style review, real penetration testing before launch.

**Phase 10 — Frontend** — 🟡 Partly done by design (server-rendered UI is complete and functional). ⏳ Open (optional): a separate SPA frontend, only if the client wants one later.

**Phase 11 — Testing & QA** — 🟡 Partly done. Tests exist and now actually pass. ⏳ Open: no CI pipeline; test coverage for the bug classes found in Phase 0.5 (exception handling, permission boundaries) should grow.

**Phase 12 — Deployment** — ⏳ Not done. Configs exist but are unverified against a real server; blocked on client-provided domain/hosting details, and TLS is not yet configured in `nginx.conf`.

---

## 10. Decisions/inputs only the client can provide

Tracked live in `client tasks.md` at the project root — do not duplicate that list here. As of this session it includes: real NOWPayments (or other) credentials; production SMTP credentials; domain/hosting/VPS details; DNS/SSL decision; legal/licensing/jurisdiction/compliance requirements; branding assets; legal texts; KYC policy; odds display format; default limits/commission rates; which sports/leagues to offer; admin alert emails; backup retention policy; public API decision; mobile/PWA decision; multi-language requirement; responsible-gambling policy; regulatory reporting requirements.

---

## 11. Rules for any AI or developer continuing this project

1. **Read this file first**, not the raw chat history.
2. **Verify by running the code, not just reading it.** This project's own history (Phase E vs. Phase F above) is the proof that reading code and git history can produce a confident, well-organized, and still-wrong picture of what actually works. Run the tests. Try the actual user flow. Reproduce a bug before believing it exists, and reproduce a fix before believing it works.
3. **Keep this file updated**, but at the level of "what's the current position and what's the goal," not a line-by-line changelog. Commit messages and git history are where the mechanical detail belongs.
4. **Business logic belongs in each app's `services.py`, never in views.**
5. **Every wallet-changing operation must be an atomic DB transaction with row locking** — money correctness is the client's top priority.
6. **Keep the provider adapter pattern** for odds and payments — new providers are new classes implementing the existing interface, never hard-coded into business logic.
7. **Never commit real secrets** into any file other than the gitignored `.env`. This has already happened once (Section 6) — don't repeat it.
8. **Never leave a development/testing shortcut reachable by real users.** The self-funding exploit fixed this session started life as exactly that. If you need a dev convenience, gate it behind `settings.DEBUG` or an explicit admin-only, audit-logged action — never a plain user-facing endpoint.
9. **The client wants large, complete units of work** delivered without stopping to ask permission mid-task — use good judgment, but bias toward finishing a full, coherent, *verified* feature rather than a half-step.
10. **Don't silently invent requirements.** Where something is genuinely undecided, it belongs in `client tasks.md`.
11. **Never commit to git unless explicitly asked to**, even when asked to "fix everything" — fixing code and committing it are different permissions.
12. **Never put a custom URL under the `admin/` prefix.** Django's built-in admin claims that whole prefix with a catch-all view; anything else routed under it is silently unreachable. The custom admin section lives at `/panel/...` for exactly this reason (Section 4.9) — keep it there.
13. **Reading code and passing unit tests are not the same as the feature working.** This project's history includes a fully-implemented, unit-tested-adjacent admin section that was completely unreachable in the browser the whole time, and a codebase that "looked done" while having a live self-funding exploit. When in doubt about whether something really works, click through it as a user would, or reproduce it with a script that hits the real URL — don't stop at reading the source.
