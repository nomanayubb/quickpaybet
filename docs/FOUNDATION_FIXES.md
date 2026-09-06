# Foundation Fixes — Working Checklist (COMPLETE — 2026-09-06)

**Status: all items below fixed and verified** (Django test suite: 18/18 passing; every critical item also reproduced live before the fix and re-verified live after). This file's job is done — see `PROJECT_MASTER_DOCUMENTATION and progress done until now.md` for how the project continues from here. This file is kept only as a record of what this pass covered.

**Purpose:** this file exists only for the current foundation-hardening pass. It lists every concrete issue found during the 2026-09-06 audit, described in enough detail that work can resume from here even in a fresh session, without needing to re-read the whole codebase. Once the foundation is solid, this file's job is done — the master documentation file (`PROJECT_MASTER_DOCUMENTATION and progress done until now.md`) is what continues forward, updated with real progress, not this checklist. This file is not meant to be maintained forever.

Status markers: `[ ]` not started, `[~]` in progress, `[x]` done and verified (tests re-run / behavior reproduced fixed).

---

## CRITICAL — live financial/security holes

- [x] **Wallet self-funding exploit.** `apps/web/views.py:confirm_deposit_view` + its URL `wallet/confirm-deposit/<id>/` let any logged-in user mark their OWN pending `CryptoPayment` as confirmed, which calls `handle_deposit_success()` and credits their wallet — no real payment, no provider webhook, no admin. Reproduced live: an account credited itself $999,999 with zero real payment. **Fix:** remove this endpoint from user-reachable code entirely. Deposits must only ever be confirmed by `apps/payments/views.py:PaymentWebhookView` (which verifies the provider's signature) or, for `mock`/dev use, an explicit admin-only action in Django admin, gated and audit-logged. Also update `web/wallet.html` template if it links to the removed endpoint.

- [x] **Withdrawals don't actually pay out.** `apps/wallet/views.py:WithdrawView` and `apps/web/views.py:wallet_withdraw_view` both call `wallet.services.withdraw_funds()` directly: deducts balance, marks the `WalletTransaction` `COMPLETED` immediately, but never creates a `CryptoPayment` row and never calls the payment provider's `create_withdrawal()`. Real money (or the record of it) is never actually sent anywhere. **Fix:** add a `create_withdrawal_request()` service in `apps/payments/services.py` that: (1) calls `withdraw_funds()` to deduct+ledger atomically, (2) creates a `CryptoPayment` (type=withdrawal, status=pending), (3) calls `get_payment_provider().create_withdrawal(...)`, (4) if the provider call fails, refunds the wallet and marks the payment `failed` rather than leaving the user's money in limbo. Update both `WithdrawView` (API) and `wallet_withdraw_view` (web) to call this instead of the raw wallet service. Also make `NOWPaymentsProvider.create_withdrawal()` a real best-effort implementation instead of a hardcoded fake-success placeholder — it should never claim success without an actual provider response, and should mark `pending` if the provider requires manual/2FA approval (which NOWPayments payouts typically do).

- [x] **Privilege escalation in user management.** `apps/accounts/views.py:AdminUserUpdateView` (API) and `apps/web/views.py:admin_user_update_view` (web) both let anyone with role `master` (not just full admins) edit **any** user by ID/PK — no check that the target is their own downstream user — and can set `role` to `admin`, reassign `parent` to anyone, and edit any commission/limits. **Fix:** add `user_can_manage_target()` + `roles_assignable_by()` helpers (object-level: full admins/staff can manage anyone and assign any role; masters can only manage users whose `parent_id == master.id`, and can only assign `agent`/`user` roles, never reassign `parent`). Enforce via a `CanManageTargetUser` object permission on the API view, validation in `AdminUserUpdateSerializer`, and equivalent checks in the web view. Also scope `admin_users_view`'s listing so masters see only their own children (full admins still see everyone).

- [x] **Site broken without a manually-created `.env`.** No `.env` file exists anywhere in the repo (correctly gitignored, but also never created). Without it, `DEBUG` defaults to `False` (safe default), which forces `SECURE_SSL_REDIRECT=True` in `config/settings.py` — so every plain HTTP request 301-redirects instead of being served, breaking the site and the test suite entirely outside of `docker-compose.yml` (which hardcodes `DEBUG=True`). **Fix:** make `SECURE_SSL_REDIRECT` its own explicit env var (default `False` when unset, so a fresh checkout is at least *usable*, even though not yet HTTPS-hardened) rather than silently derived from `DEBUG`; document in `.env.example`; create a real local `backend/.env` (gitignored, not committed) for this machine so local dev/tests work correctly.

- [x] **Web views crash with HTTP 500 on any betting validation error.** `apps/web/views.py` imports and catches `django.core.exceptions.ValidationError`, but `apps/bets/services.py` (`place_bet`, `place_parlay_bet`) and `apps/wallet/services.py` (`withdraw_funds`, `deposit_funds`) all raise `rest_framework.exceptions.ValidationError` — a different class. Reproduced live: betting more than your balance 500s instead of showing "Insufficient balance." Affects `match_detail_view`, `place_bet_view`, `parlay_bet_view`, `wallet_withdraw_view`, `wallet_deposit_view`. **Fix:** catch the correct exception type (import from `rest_framework.exceptions`) everywhere in `apps/web/views.py` that calls into a service function, or normalize by catching both.

---

## SIGNIFICANT — real gaps, not exploits

- [x] **Audit log never written to.** `apps/audit/services.py:create_audit_log()` exists, has a Django admin page and an API endpoint to view it, but is never called anywhere in the codebase — grep confirms zero call sites outside its own definition. **Fix:** call it from the money/admin-sensitive actions: payment webhook success, the new withdrawal-request flow, admin match settle/cancel (both web views and `apps/sports/admin.py` actions), admin user role/limit updates, and the odds-override endpoint.

- [x] **`sync_results_celery` Beat task is a no-op stub.** It's scheduled every 30 minutes in `config/celery.py` but its body in `apps/sports/tasks.py` just logs and returns `True` — it never calls `sync_results`. **Fix:** mirror `sync_odds_celery` and actually run `call_command('sync_results')`.

- [x] **`select_for_update()` evaluated outside an explicit `atomic()` block.** In `apps/bets/services.py`, `settle_parlays_for_match()` (the `.exists()` call) and `settle_bets_for_match()` (the `for bet in pending_bets:` loops) evaluate a `select_for_update()` queryset without an enclosing `transaction.atomic()`. This happened to not crash on SQLite (which silently ignores `FOR UPDATE`) but is not guaranteed safe on PostgreSQL, which is the intended production database. **Fix:** wrap these query evaluations in explicit `atomic()` blocks.

- [x] **Duplicate `IsAdminOrMaster` permission class** defined separately (identical code) in `apps/accounts/permissions.py`, `apps/sports/permissions.py`, `apps/reports/permissions.py`, `apps/audit/permissions.py`. **Fix:** canonical version now lives in `apps/common/permissions.py` (already created) — update the other three to import from there.

- [x] **Leaked API key in committed docs.** A real Odds API key is in plaintext in `docs/CHANGE_LOG.md`. **Fix:** redact it from the file (done/being done this pass). Note: it's still in git history — the client/developer should rotate the actual key with the provider; that's not something fixable by editing a file.

- [x] **No `celery beat`/`celery worker` service in either `docker-compose.yml` or `docker-compose.prod.yml`.** The schedule exists in code (`config/celery.py`) but nothing runs it unless a human manually starts those processes. **Fix:** add `celery-worker` and `celery-beat` services to both compose files.

- [x] **Broken test:** `apps/bets/tests.py:test_commission_credited_on_loss` calls `deposit_funds(parent, Decimal('0'))`, which `deposit_funds()` correctly rejects (amount must be positive) — the test itself is wrong, not the code under test. **Fix:** correct the test to deposit a positive amount (or skip the deposit if the point of the test doesn't require the parent to have a starting balance).

- [x] **Found only by live testing, not in the original audit: the entire custom web admin section was unreachable.** `apps/web/urls.py` mounted the custom admin pages (users, matches, sports, reports, audit) under `admin/...`, but `config/urls.py` registers Django's own `admin.site.urls` at `admin/` *first*, and Django's built-in `AdminSite.catch_all_view` intercepts every URL starting with `admin/` before it can reach any other urlconf — silently redirecting non-staff users to `/admin/login/` and 404ing for everyone else. This meant `/admin/users/`, `/admin/matches/`, `/admin/sports/`, `/admin/reports/`, `/admin/audit/` never worked, for anyone, the whole time. **Fix:** moved the entire custom admin section from the `admin/` prefix to `panel/` in `apps/web/urls.py` (URL *names* unchanged, so all `{% url %}`/`reverse()` call sites kept working with no template changes needed). Verified live: all `/panel/...` pages now return 200 for admins/masters and correctly redirect non-privileged users.

- [x] **Pre-existing migration drift.** `apps/wallet/models.py`'s `WalletTransaction.txn_type` choices didn't match the last migration (`makemigrations --check` failed). Harmless in SQLite (choices aren't a DB constraint) but real drift. **Fix:** generated and applied `apps/wallet/migrations/0002_alter_wallettransaction_txn_type.py`. `makemigrations --check` is now clean across the whole project.

---

## MINOR / hygiene — do last, lowest risk

- [x] Repo-root and `web/` junk files (stray filenames from commands typed to disk in earlier sessions — confirmed to contain no real content, checked before deleting): `$env`, `un.txt`, `1. Apply the migration...`, `docker compose up --build`, `python manage.py migrate`, a duplicate `docs/New folder/PROJECT_MASTER_DOCUMENTATION.md`, etc. All deleted from disk.
- [x] Git index cleaned up: unstaged the leftover entries for files that were `git add`-ed in an earlier session but never committed and no longer exist (`docs/PROJECT_MASTER_DOCUMENTATION.md`, two `python manage.py migrate...` files, one unicode-named fragment). `git status` is now an honest, readable list of real deletions (`D`) and real code changes (`M`) with no commit made — **committing is still the client's/developer's call, not done automatically.**

---

## Verification approach for every item above

Don't mark an item `[x]` from reading the diff alone. For each one: re-run the relevant Django test(s) (`manage.py test`), and where the bug was reproduced live with a shell script during the audit, re-run an equivalent script to confirm the fixed behavior (e.g., the self-confirm deposit script should now fail/be rejected, not silently succeed).
