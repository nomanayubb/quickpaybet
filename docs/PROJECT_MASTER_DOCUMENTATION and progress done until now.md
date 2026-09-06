# QuickPayBet — Project Master Documentation

**Purpose of this file:** this is the single source of truth for the QuickPayBet project. Any developer or AI assistant picking up this project should read this file first, before touching any code. Only dig into `.aider.chat.history.md` (a 3.2 MB raw chat log from the original build sessions with DeepSeek/aider) if you need an exact original quote — it is not required reading and is error-prone to parse.

**How to keep this file updated (rule for every future session, human or AI):** update this file when you finish meaningful work — a new capability, an architectural decision, a phase completed, a real gap discovered. Do **not** log every individual bug fix, error message, or minor edit here — that belongs in commit messages, not here. This file answers three questions: *what path has the project traveled so far, where does it actually stand right now, and what is the goal it's heading toward.* Keep it narrative and honest, not a changelog.

Last compiled: 2026-09-07, by Claude Code. Originally written 2026-09-06 after a full line-by-line audit of the actual codebase (not just the chat history) plus live reproduction of bugs by running the code; kept current since as real work has landed, most recently the betting exchange (Section 4.11).

---

## 0. Read this first — the path, where we are, and where we're going

**The path so far:** the client (goal.txt) asked, in a long free-form brief, for a professional, fully-automated sports betting platform in Python/Django: real-time odds a user can bet on, automatic settlement, automatic crypto payment verification, a hierarchical admin/master/agent/user system with configurable limits and affiliate commissions, strict security around odds/money, and a codebase modular enough to bolt on new payment providers, odds providers, and even casino games later without rewrites. Budget was tight — free/cheap tools only.

That brief was handed to DeepSeek (via `aider`) over roughly 13 chat sessions on 2026-09-05, which produced 73 git commits: a Django project with 9 apps, a working wallet ledger, a real (free-tier) odds provider integration, single and parlay betting, an affiliate commission system, a server-rendered web UI, and Docker/Nginx deployment configs. That work is real and mostly well-structured — it is not a toy. A previous documentation pass (also on 2026-09-05) read all 73 commits and the chat log and produced an accurate architectural snapshot of what had been *built*.

**Where we actually stand (as of 2026-09-06, verified by running the code, not just reading it):** that architectural snapshot was correct about what was *built*, but nobody had actually run the test suite or exercised the live app end-to-end. Doing that surfaced several foundation-level problems that don't show up by reading code alone: a live financial exploit (users could credit their own wallet with unlimited fake money), withdrawals that silently didn't pay out, a privilege-escalation hole in user management, and the site returning HTTP errors instead of running at all without a manually-created `.env` file. None of this was malicious — it reads as leftover developer/testing shortcuts that were never removed before being treated as "done" — but it means **the project was not actually at a safe, working foundation**, despite looking complete from the outside (working URLs, passing-looking feature list).

**Current position:** the foundation-hardening pass described above is complete (2026-09-06) — every exploit, gap, and correctness bug found is fixed and re-verified live, the full test suite passes, and the code is on GitHub (`nomanayubb/quickpaybet`, private repo, CI running). Since then, work has moved into "build what's next," and the client has directed a lot of it: a proper admin Users/Ledger system with real search/filter/export, Pakistan-time handling for the admin panel vs. per-visitor timezones on the public site, a manual NOWPayments payout tool, public-site match history and bet-status views — and, as of 2026-09-07, **a full Betfair-style back/lay betting exchange** (`apps/exchange`, see Section 4.11) alongside the original fixed-odds sportsbook. That's the single largest feature in the project so far; it was built behind a formal plan-and-approval step (given as much money-correctness risk as everything else in this document combined) and is fully tested (32 automated tests project-wide, exact settlement math verified, not just "no crash") before being considered done.

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
`CryptoPayment` model. `providers.py`: `BasePaymentProvider` interface, `MockPaymentProvider` (default), `NOWPaymentsProvider` (real API calls + HMAC webhook verification, plus `authenticate()`/`verify_payout()` for the manual admin payout flow). `services.py` creates a pending `CryptoPayment` and, on a **provider-verified webhook**, credits the wallet; `create_withdrawal_request()` (the automatic path used by the API/web withdraw forms) deducts+ledgers atomically, calls the provider, and auto-refunds if the provider rejects the payout — money can never be silently lost or fake-credited; `initiate_manual_payout()`/`confirm_manual_payout()` (the admin-driven path, see `apps/web/views.py:admin_withdrawal_process_view` and `/panel/withdrawals/`) handle the real NOWPayments 2FA-per-batch flow. **Withdrawals did not actually call the provider at all before this session — fixed; see Section 8.**

**Live-tested against the real NOWPayments account (2026-09-06):** deposit creation works end-to-end (confirmed: a real deposit address was generated). The IPN secret was also provided and tested — and testing it caught a real bug: `verify_webhook()` was hashing the raw request body directly, but NOWPayments actually signs the JSON **re-serialized with recursively sorted keys** (`json.dumps(data, separators=(',', ':'), sort_keys=True)`) before hashing. The old code would have silently rejected essentially every real webhook, meaning deposits would never have auto-confirmed once a real provider went live. Fixed and confirmed correct with a realistic simulated webhook (accepts a correctly-signed payload, rejects a tampered one).

**Payouts — a manual admin tool, not full automation, and that's intentional.** NOWPayments requires a 2FA code to release *every single payout batch* — this is confirmed from reading their own official SDK source (`NowPaymentsIO/nowpayments-sdk-nodejs` on GitHub), not guessed. That means real, hands-off, zero-touch automatic withdrawals are not something NOWPayments allows at all, for anyone — a human has to be in the loop for every payout, by their design. Building around that would mean defeating the exact security control that protects the client's funds, which this project won't do.

What was built instead: `/panel/withdrawals/` (admin-only) lists pending withdrawal requests; "Process" walks an admin through (1) NOWPayments email+password entered directly into that page — sent straight from the admin's browser to this server to this server's call to NOWPayments' `/v1/auth`, never stored, never logged, never seen by an AI assistant — which creates the payout batch, then (2) the 2FA code from an authenticator app to confirm it via `/v1/payout/<batch_id>/verify`. The exact request/response shapes for `/v1/auth`, `/v1/payout`, and the verify endpoint were confirmed by reading NOWPayments' own official SDK source code on GitHub (not guessed, not scraped from a JS-rendered docs page that wouldn't load) — `authenticate()` was live-tested against the real API with intentionally wrong credentials and got back NOWPayments' real "Incorrect login or password" error, confirming the request format is correctly understood by their server. **The full flow has not yet been tested with a real password + real 2FA code** — that step can only be done by the client themselves, ideally with a small test amount first (tracked in `client tasks.md`).

The fully-automatic withdrawal path (`create_withdrawal_request`, used by the wallet API/web withdraw forms) still correctly and safely fails without a JWT and auto-refunds the user — that's expected, not a bug: those paths never have a password to work with, by design.

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

### 4.11 `apps/exchange` — Betfair-style back/lay betting exchange (added 2026-09-07)
A second, completely separate betting mode alongside the fixed-odds sportsbook in `apps/bets`: instead of betting against the house at admin-set odds, users bet *against each other* by placing **back** (for) or **lay** (against) orders at odds *they choose*, and the platform matches compatible orders together (a real limit order book, price-time priority — not a simplified "house is the counterparty" shortcut).

- `models.py` — `ExchangeConfig` (singleton row, admin-editable commission % on winnings, default 5%, via `ExchangeConfig.get_solo()`); `ExchangeOrder` (`user`, `match`, `selection` home/draw/away, `side` back/lay, `odds`, `stake`, `matched_stake`, `status`); `ExchangeFill` (one matching event between exactly one back order and one lay order — `odds` here is always the *resting* order's price, `commission_amount` recorded for audit).
- `services.py` — `place_order()` (validates, reserves exposure, then matches against the book — **back** exposure = stake; **lay** exposure = `stake*(odds-1)`, its liability if the backed outcome happens; reuses the previously-unused `Wallet.reserved_balance`/`available_balance` fields, no wallet schema change needed); `cancel_order()` (releases only the unmatched remainder — already-matched fills stand); `settle_exchange_for_match()` (mirrors `apps.bets.services.settle_bets_for_match()`, called from the exact same call sites — `sync_results`, the admin settle/cancel actions in both `apps/sports/admin.py` and `apps/web/views.py`, and the Celery settlement task — so it runs automatically with zero new scheduling); `get_order_book()` (aggregated prices for display).
- **A subtle but important design point, worth remembering if this code is ever touched again:** exposure reserved/released always uses each order's *own* requested odds, while actual profit/loss paid between the two matched users always uses the *fill's* execution odds (which can be a better price than either side originally asked for — "price improvement"). Mixing these up would either leave `reserved_balance` slowly drifting wrong over time, or shortchange/overcharge one side of a fill that got matched at an improved price. Verified explicitly with a dedicated test (`test_price_improvement_backer_gets_better_price`) and with exact-number settlement assertions, not just "it ran without crashing."
- Self-matching (a user's own resting order matching their own new order) is deliberately excluded in the matching query.
- **Web UI**: an "Exchange" section on the match detail page (order book, place-order form, cancel your own open orders), a new "My Exchange Bets" page, and "Exposure" surfaced on the wallet page. No admin exchange dashboard yet, and no cash-out (selling a matched position before the match ends) — both explicitly deferred to a later phase, tracked as open in Section 8.
- Built behind a formal plan-and-approval step given the money-correctness stakes (on the client's own insistence — "this project is not a joke... we cannot risk website"), with 14 dedicated tests (exact-price match, price improvement, partial fills across multiple counterparties, no-match, self-match exclusion, insufficient-balance rejection, cancel behavior, and settlement math for both back-wins and lay-wins scenarios verified to the exact expected wallet number) plus live HTTP-level verification (real page loads, real form submissions, real admin settle action) before being called done.

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
- **Parlay bet decimal overflow.** `ParlayBet.total_odds` is `DecimalField(max_digits=10, decimal_places=2)` (max ~100 million), but nothing capped how many selections a parlay could have or how large the multiplied combined odds could get. Reproduced: a 30-leg parlay at 2.00 odds each computes a combined odds value in the billions — SQLite silently stored the wrong, truncated-precision value instead of erroring; PostgreSQL (the real production database) would hard-crash with a numeric overflow error. Fixed in `apps/bets/services.py:place_parlay_bet()` with both a sane leg cap (12) and a hard ceiling check on the computed combined odds, checked before anything is saved. Confirmed: the bad case is now rejected with a clear message, normal parlays (verified with 5 legs) are unaffected.
- **Explicitly declined: storing recoverable/plaintext passwords.** The client asked for this (to relay forgotten passwords via WhatsApp for less tech-savvy users) and referenced a mistaken belief that Firebase shows admins plaintext passwords (it doesn't — it hashes them like everyone else). Declined and explained why (a single database breach would leak every user's password in usable form, and people reuse passwords across sites, so the blast radius extends beyond this platform). Built the safe equivalent instead: a superuser-only "Reset password" action (`apps/web/views.py:admin_reset_user_password_view`) that generates a brand-new random password, shown once on screen to relay manually (e.g. via WhatsApp) — the user's old password is never seen or recoverable by anyone, a new one is simply created. This is the one place in this project where an explicit client request was not implemented as asked; if a future session gets a similar request, the answer is the same, for the same reason.
- **Phone numbers and password resets are now superuser-only.** Added `User.phone_number` (optional, collected at registration, editable by admins). Per the client's explicit request, both phone number visibility and the password-reset action are gated to `is_superuser` accounts only — a staff member with `role=admin` (but not the actual superuser/owner account) sees neither. Verified live: a non-superuser admin viewing a user's detail page sees no phone number and no reset button; attempting the reset URL directly is redirected, not processed.
- **New admin feature: full per-user history page** (`/panel/users/<id>/`) — profile, wallet balance, every transaction, every deposit/withdrawal, every single and parlay bet, direct downstream users. Requested after live testing revealed the Manage Users page could only edit a user, never actually look at their history.
- **Users tab overhaul (2026-09-06):** the Manage Users list (`/panel/users/`) now has a search box (by email), a date-joined range filter, a sort dropdown (newest account, most recent first/last bet, most wins/losses, most deposited/withdrawn today or all-time), and a filter dropdown (currently signed in, has a pending bet, won/lost their first or most recent bet). Small "AC / FB / LB" columns show account-created / first-bet / last-bet dates compactly. Each row has an "Info" link (the existing detail page) and a new "Ledger" link.
  - **Correctness note (important for future work on this query):** combining `Count()`/`Sum()` annotations over two different reverse relations (`bets` and `crypto_payments`) on the same `User` queryset causes Django to JOIN both tables, which silently inflates every aggregate via row fan-out — every bet row gets multiplied by every payment row for that user. This was caught before shipping, not after: every single/combined aggregate on this page (won/lost/pending counts, first/last bet date+status, deposit/withdraw sums today and all-time) was rewritten as an independent correlated subquery (`Subquery(...).order_by().values(...).annotate(...).values(...)[:1]`) specifically to avoid this, and reverified against known test data (exact expected numbers, not just "it returned 200") after the rewrite. Anyone adding a new per-user metric to this page must use the same subquery pattern, not a direct `Count`/`Sum` annotation, the moment more than one relation is involved.
  - **"Currently signed in"** is defined honestly, not approximated: it's computed by scanning `django.contrib.sessions.models.Session` for unexpired sessions and decoding `_auth_user_id` — a user counts as online for exactly as long as their session is valid, which is what "logged in" actually means in a session-based app (there's no separate presence/heartbeat system, nor should there be one for what this needs).
- **Timezone handling (2026-09-06):** the entire admin panel (`/panel/...`, `/dashboard/`) now always displays and filters in **Pakistan time (Asia/Karachi)**, regardless of server or viewer location, per explicit client requirement. The public/user-facing site displays in **each visitor's own browser-detected timezone** instead (a small script in `base.html` detects it via `Intl.DateTimeFormat` and stores it in a `user_tz` cookie; `apps/common/middleware.py:TimezoneMiddleware` reads that cookie and activates it per-request; a brand-new visitor's very first page load falls back to UTC before the cookie exists, then reloads once).
  - **A genuinely important bug caught here, not shipped:** Django's ORM converts a *naive* datetime used in a query filter using the **static** `settings.TIME_ZONE` (UTC) — **not** whatever timezone is currently `activate()`-d for the request. This is easy to assume is otherwise (display formatting *does* correctly use the active timezone — verified separately) and the two behaving differently is exactly the kind of thing that produces silently-wrong date-range filters. Confirmed the wrong behavior empirically first (a filter for "≥ 14:00" on a bet stored at exactly 14:00 PKT incorrectly excluded it), then fixed every admin date-range filter (`apps/web/views.py:_parse_pkt_date_bound`, used by both the Users list and the Ledger page) to explicitly call `timezone.make_aware(parsed, PAKISTAN_TZ)` rather than relying on activation, and reverified the exact-boundary case was then correct in both directions.
  - AC/FB/LB columns and all date-range filters now include time (`datetime-local` inputs), not just date, per explicit request.
- **Fixed the browser "Back" button showing a stale, wrong-account page after switching accounts.** Reported live: switching from a user login to an admin login in the same browser correctly updates the session (confirmed by clicking any link), but pressing Back showed the old user-account page again — a browser "back/forward cache" snapshot, not a real second session. Every page is session-dependent here, so every response now explicitly sends `Cache-Control: no-store, no-cache, must-revalidate, private` + `Pragma: no-cache` (`apps/common/middleware.py:NoBrowserCacheMiddleware`), forcing the browser to always re-check with the server instead of showing a memorized page. Static assets (`/static/...`) are deliberately excluded and stay cacheable. Verified live: real pages now carry the header, static paths don't.
- **The user Info page's four history boxes** (wallet transactions, deposits/withdrawals, single bets, parlay bets) each got their own independent search box, Pakistan-time date range, and pagination (25/page), prefixed `wt_`/`cp_`/`sb_`/`pb_` so their query params never collide on one page. Verified precisely with real data (not just "the page loads") — a search for a specific team name returns exactly the one matching bet, confirmed by checking the queryset directly.
- **New admin feature: per-user Ledger page** (`/panel/users/<id>/ledger/`) — two independent sections, bet history and deposit/withdrawal history, each with its own search box, date range, and adjustable page size (10 to 10,000). Page size is capped for on-screen rendering (so the browser can't be told to render 10,000 rows and hang) but CSV export of either section is uncapped, since a file download isn't rendered HTML. Clicking a bet opens `/panel/bets/<id>/` — full detail plus how many times that user has bet on that match. There's no "lay/back" concept to show (this is a traditional sportsbook — single/parlay bets on home/draw/away — not a betting exchange), so the detail page shows selection, odds, stake, and result instead.
- **Repo-wide pattern worth remembering:** several of the bugs found in this project (slug format on `Sport`, odds validation on `Match`, this decimal overflow on `ParlayBet`) share the same root cause — code calling `Model.objects.create()` directly bypasses Django's field-level validators entirely; those only run through `full_clean()` or a serializer/form. Direct `.create()` calls need their own explicit validation; don't assume the model field definition alone protects the data.
- **Public site enhancements (2026-09-06):** the public **Matches** page (`/matches/`) now has three views — Upcoming & Live (default), Results (finished matches from the last 7 days), and Full Match History (everything, any sport, any age) — plus an optional sport filter (football, cricket, whatever is active). The public **My Bets** and **My Parlay Bets** pages now have status tabs (Open / Won / Lost / Cancelled / All) with a one-line plain-language explanation of what each status means, and both are now ordered most-recent-first (they weren't ordered at all before). Verified precisely with real data: an upcoming match, a match finished 2 days ago, and a match finished 20 days ago were each shown/hidden exactly as expected across all three match views and the sport filter; a pending bet correctly appears under "Open" and correctly disappears under "Won".
- CI (GitHub Actions) is live and running — the project is now pushed to a private GitHub repository (`nomanayubb/quickpaybet`) as a real backup, with tests running automatically on every push.

*(Exact diffs are in git history/commit messages once committed — nothing has been committed yet; that's deliberate, see Section 11 rule 11 — this file records that it's done and roughly what "done" means, not the mechanics of the fix.)*

### Still genuinely open
- **Real crypto deposits work end-to-end (live-tested 2026-09-06, including webhook signature verification); real crypto withdrawals don't yet.** `PAYMENT_PROVIDER` still defaults to `mock` for local dev. Withdrawals need the client to provide their NOWPayments account email + password + 2FA method (a separate credential from the API key — tracked precisely in `client tasks.md`) so the `/payout` endpoint's JWT auth can be implemented; until then, withdrawal attempts safely fail and auto-refund rather than losing money.
- **No `celery beat`/`celery worker` service is defined in `docker-compose.yml` or `docker-compose.prod.yml`.** The schedule exists in code but nothing runs it unless someone starts those processes manually.
- **The Odds API free tier doesn't return data for most league keys** — confirmed plan/API limitation, not a code defect. Verify which `provider_key`s actually return data before promising specific leagues to end users.
- **No separate frontend** (React/Next/Vue) — current UI is server-rendered Django templates. Works, but not required to change unless the client asks.
- **No TLS/443 configured in `nginx/nginx.conf`** — production as currently configured has no real HTTPS termination path yet; blocked on the client's domain/hosting decision (`client tasks.md`).
- **No production deployment has happened.** Configs look ready but are unverified against a real server.
- **Exchange (`apps/exchange`) Phase 2, not built yet by design:** cash-out (selling a matched position before the match ends), an admin-facing exchange dashboard/report, advanced order types. See Section 4.11.

---

## 9. Full phased roadmap

**Phase 0 — Documentation & Approval** — ✅ Done.

**Phase 0.5 — Foundation Hardening** — ✅ Done this session (Section 8's "fixed" list). This phase didn't exist in earlier roadmaps because the problems it fixes weren't known until the code was actually run.

**Phase 1 — Repository & Backend Foundation** — ✅ Done.

**Phase 2 — Authentication & Permissions** — ✅ Done, including the hierarchy-scoping fix from Phase 0.5.

**Phase 3 — Sports / Matches / Odds** — ✅ Mostly done. ⏳ Open: confirm which league keys return real data on the current API plan; get the scheduler actually running in Docker (Phase 0.5 fixed the stub task, but nothing starts `celery beat` yet).

**Phase 4 — Wallet & Crypto Payments** — 🟡 Partly done, significantly more so after 2026-09-06. Ledger + mock flow work end-to-end and are honest about what they do (Phase 0.5). Real NOWPayments **deposits are fully live-tested and working end-to-end, including webhook signature verification** (a real bug in the signature algorithm was found and fixed while testing this — see Section 4.5). A manual-payout admin tool (`/panel/withdrawals/`) is built, request-format-verified against the real API, but not yet exercised with a real password + 2FA code (needs the client to do that once — see `client tasks.md`). Automatic zero-touch payouts are not something NOWPayments allows at all (2FA is required per batch, by their design) — this is a permanent characteristic of the provider, not an open gap to close later.

**Phase 5 — Betting Engine** — ✅ Mostly done, plus a full second betting mode added 2026-09-07: a Betfair-style back/lay exchange (`apps/exchange`) alongside the original fixed-odds single/parlay sportsbook — real order matching, exposure, commission, settlement, all tested (Section 4.11). ⏳ Open: cash-out, an admin exchange dashboard.

**Phase 6 — Settlement Automation** — ✅ Mostly done, more honestly so after Phase 0.5 (the scheduling stub is fixed). ⏳ Open: an actual running `celery beat` + `celery worker` process in the deployment stack.

**Phase 7 — Admin Panel Custom UI** — ✅ Mostly done, now with correct hierarchy-based access control (Phase 0.5).

**Phase 8 — Reports & Analytics** — 🟡 Partly done. ⏳ Open: drill-down by sport/user/match beyond what exists, payout/profit trend views.

**Phase 9 — Security & Hardening** — 🟡 Improved significantly this session (Phase 0.5, plus a follow-up hardening pass same day): brute-force protection now on API login/register/password-reset (10/hour) in addition to the web login's existing lockout; a dedicated `withdraw` throttle scope; odds-override endpoints now reject non-positive/nonsensical odds (a real gap that existed with zero validation before). ⏳ Open: CORS tightened to a real origin once the client picks a domain, a broader input-validation audit beyond what's been checked so far, an OWASP-style review, real penetration testing before launch.

**Phase 10 — Frontend** — 🟡 Partly done by design (server-rendered UI is complete and functional). ⏳ Open (optional): a separate SPA frontend, only if the client wants one later.

**Phase 11 — Testing & QA** — ✅ Mostly done. Tests exist, pass, and CI (GitHub Actions) runs them automatically on every push. 32 tests project-wide as of 2026-09-07 (18 foundation + 14 for the exchange). ⏳ Open: test coverage for the bug classes found in Phase 0.5 (exception handling, permission boundaries) should keep growing as new features land.

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
14. **`Model.objects.create()` does not run field validators.** Only `full_clean()`, a `ModelForm`, or a DRF serializer does. Several real bugs in this project (an unvalidated `Sport.slug`, unvalidated `Match` odds, an unbounded `ParlayBet.total_odds` overflow) all came from code creating a model instance directly and assuming the field definition itself was protection. It isn't — validate explicitly wherever a model is created directly from user input.
15. **Never store recoverable/plaintext passwords, no matter who asks or why.** This was explicitly requested once (to relay forgotten passwords via WhatsApp) and declined — the risk isn't about who's allowed to see it in the admin UI, it's that the data exists in recoverable form at all, which turns any future breach, stolen backup, or compromised admin account into an instant leak of every user's password. Build the safe equivalent instead (a reset-to-a-new-password action) if a similar request comes up again.
16. **Combining `Count()`/`Sum()` annotations over two different reverse relations on one queryset causes row fan-out and silently wrong numbers**, not an error — Django JOINs both relations and multiplies rows together. Every per-user metric on the Users/Ledger pages was rewritten as an independent correlated subquery specifically because of this, and reverified against known test data with exact expected numbers. Use the subquery pattern (`Subquery(Model.objects.filter(...).order_by().values('group_field').annotate(x=...).values('x')[:1])`) the moment a second relation is involved, not a direct annotate.
17. **Django's ORM does NOT use the currently-`activate()`-d timezone when converting a naive datetime for query filtering — it uses the static `settings.TIME_ZONE`.** Template *display* of a datetime *does* respect the active timezone (verified separately) — the two behave differently, which is exactly what makes this easy to get wrong without noticing. Any code that builds a filter value from a naive datetime and needs it interpreted in a specific timezone (e.g. the admin panel's Pakistan-time date filters) must call `timezone.make_aware(naive_dt, that_timezone)` explicitly — see `apps/web/views.py:_parse_pkt_date_bound`.
18. **When two parties settle money between each other at a negotiated/matched price (the exchange's back/lay fills), keep "how much was reserved/released" and "how much money actually moves" as two separately-computed things, even though they're related.** Reserve/release using each side's own originally-requested terms (so the bookkeeping always nets to exactly zero, no drift, regardless of what price a match actually executed at); compute the real profit/loss using the actual executed terms (fair to both parties, especially when one side got a better price than they asked for - "price improvement"). Conflating the two either lets `reserved_balance` drift over time or shortchanges/overcharges a matched party. See `apps/exchange/services.py:_exposure_rate()` / `_settle_fill()` and the dedicated test proving this (`test_price_improvement_backer_gets_better_price`).
19. **For anything this size or riskier, use a formal plan-and-approval step before writing code** — don't just start building. The betting exchange (Section 4.11) went through `EnterPlanMode`/`ExitPlanMode` with the client approving the exact design (data model, matching algorithm, settlement math, commission handling, phase boundaries) before any file was touched. This caught the matching-model question (house-as-counterparty vs. true peer-to-peer) and the commission question early, as design decisions, rather than as rework after guessing wrong.
