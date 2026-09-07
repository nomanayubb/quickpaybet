# Disaster Recovery: Encrypted Backups & Moving to a New Server

This is the runbook for two situations:

1. **The current domain/server gets blocked or seized** and the whole platform needs to come back up on a new domain and a new server, with every user, wallet balance, bet, and match record intact.
2. **Routine restore testing / rollback** on the existing server.

Nothing here requires the original server to still be reachable, as long as you have:
- The most recent downloaded `backup_*.json.enc` file.
- The `BACKUP_ENCRYPTION_KEY` (stored *outside* this repository and outside the server — a password manager, not a text file next to the backups).
- The `.env` secrets checklist below.

## Why this is safe to do at all

Every backup is a full database dump (Django's own `dumpdata`, not a raw Postgres/SQLite file), encrypted with a symmetric key (Fernet, from the `cryptography` package) that only ever lives in `.env` / wherever the admin has stored it — never in the repo, never in the backup file itself. Restoring a backup recreates every user with their **hashed** password intact (Django password hashes are self-contained — algorithm, salt, and hash together — so a restored user logs in with their original password with zero special handling). No plaintext password, card number, or API secret is ever written to a backup file, because none of those are stored in the database in the first place (API keys and secrets live only in `.env`, which is why the checklist below exists separately).

## What's backed up, what isn't

Included: every app's tables — users, wallets, wallet transactions, bets, parlay bets, exchange orders/fills, cashback, rewards, audit logs, matches, sports, tournaments, crypto payments, pricing/odds overrides, everything under `apps.*`.

Deliberately excluded (regenerated automatically by Django on a fresh `migrate`, not real business data): `contenttypes`, `auth.permission`, `admin.logentry`, `sessions.session`.

## Where backups come from

- **Automatic**: a Celery Beat job (`nightly-database-backup` in `config/celery.py`) runs `backup_database` every night at 03:00 server time. It keeps the 14 most recent backups on disk and deletes older ones automatically.
- **Manual**: a full admin (`is_staff`, `is_superuser`, or role `admin` — not `master`) can go to `/panel/backups/` and click "Trigger backup now". This enqueues the same Celery task rather than blocking a web request — dumping and encrypting the whole database takes real time, and production only runs 3 Gunicorn workers, so doing this synchronously in a request would tie one up.
- Backup files live in `BACKUP_DIR` (`backups/` at the project root), which is gitignored and never served as a static file — the only way to get one off the server is the authenticated "Download" link on `/panel/backups/`.

**Because there is no cloud storage account for this platform, backups only ever exist on the server until an admin downloads them.** Get in the habit of downloading the latest file from `/panel/backups/` periodically (e.g. weekly) to a local machine or external drive — a backup that never leaves the at-risk server doesn't help if that server is the thing that gets blocked.

## The `.env` checklist (secrets that live outside the database)

None of these are in a database backup — they have to be copied to the new server by hand. From `backend/.env.example`:

| Variable | Notes |
|---|---|
| `SECRET_KEY` | Django's cryptographic secret. Copy the exact value — don't regenerate it, or existing sessions/signed tokens break. |
| `DATABASE_URL` | New server's own DB credentials — this one you *do* generate fresh for the new server, then restore the backup into it. |
| `ALLOWED_HOSTS` | Must list the new domain. |
| `REDIS_URL` | New server's Redis instance. |
| `CORS_ALLOW_ALL_ORIGINS`, `SECURE_SSL_REDIRECT`, `CACHE_BACKEND` | Copy as-is unless the new server's setup differs (e.g. TLS not yet configured — leave `SECURE_SSL_REDIRECT=False` until it is). |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` | Copy as-is; `DEFAULT_FROM_EMAIL` domain part should match the new domain if it changed. |
| `ODDS_PROVIDER`, `ODDS_API_KEY`, `ODDS_API_URL`, `ODDS_SPORT_KEY` | Copy as-is. |
| `PARLAY_API_KEY`, `PARLAY_API_URL`, `PARLAY_BOOKMAKER` | Copy as-is. |
| `PAYMENT_PROVIDER`, `NOWPAYMENTS_API_KEY`, `NOWPAYMENTS_API_URL`, `NOWPAYMENTS_IPN_SECRET` | Copy as-is. |
| `NOWPAYMENTS_IPN_CALLBACK_URL` | Must be updated to point at the new domain (`https://NEW_DOMAIN/api/payments/webhook/`), and the new URL registered with NOWPayments. |
| `BACKUP_ENCRYPTION_KEY` | Copy the exact same key used to encrypt the backup you're restoring — a new server with a new key cannot decrypt an old backup. |

Nothing else in the codebase is domain-specific: `ALLOWED_HOSTS`/`SECRET_KEY`/`DATABASE_URL`/etc. are all read from `.env` via `django-environ`, `CSRF_TRUSTED_ORIGINS` is derived automatically from `ALLOWED_HOSTS`, and nginx's `server_name` is already a catch-all (`_`). Standing up the app on a new domain is genuinely just: new `.env` + new DNS + a TLS certificate.

## Step-by-step: standing up a new server

1. **Provision** the new server (or container host) and install Docker + Docker Compose.
2. **Clone the repo** (from GitHub, or copy the working tree directly — the repo itself carries no secrets).
3. **Recreate `backend/.env`** using the checklist above. Generate a fresh `SECRET_KEY` only if the old one is not recoverable (this invalidates existing sessions but nothing else); otherwise reuse it. Use the new server's own `DATABASE_URL` (a fresh, empty Postgres instance).
4. **Bring up the stack** far enough to get a database, but not the app yet:
   ```bash
   docker compose -f docker-compose.prod.yml up -d db redis
   ```
5. **Run migrations** against the fresh, empty database:
   ```bash
   docker compose -f docker-compose.prod.yml run --rm backend python manage.py migrate
   ```
6. **Copy the latest downloaded backup file** onto the new server (e.g. `scp`), and copy the matching `BACKUP_ENCRYPTION_KEY` into `.env` (already done in step 3 if you followed the checklist).
7. **Restore it** into the freshly migrated, still-empty database:
   ```bash
   docker compose -f docker-compose.prod.yml run --rm backend python manage.py restore_database /path/to/backup_YYYYMMDD_HHMMSS_ffffff.json.enc --confirm
   ```
   `restore_database` refuses to run without `--confirm` on purpose — it is destructive and only makes sense on a database with nothing in it yet. Never run it against a database that already has live data.
8. **Bring up the rest of the stack**:
   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```
9. **Verify** by logging in as an existing user (their original password should work) and checking their wallet balance and bet history match what you expect from before the outage.
10. **Point DNS** at the new server's IP once you're satisfied it's healthy.

## Key-storage warning

`BACKUP_ENCRYPTION_KEY` is the single point of failure for every backup ever taken with it:

- **Lose the key** → every existing encrypted backup becomes permanently unreadable. There is no recovery mechanism — Fernet is symmetric encryption with no backdoor.
- **Leak the key** (commit it to git, paste it somewhere public, etc.) → anyone who also gets hold of a backup file can decrypt every user's data, including wallet balances and password hashes. A leaked key should be rotated immediately: generate a new one, update `.env`, and understand that all *already-encrypted* backup files are still only as safe as whoever else might have seen the old key.

Store it in a password manager or equivalent, never in this repository, and never in the same place as the backup files themselves.

## Testing a restore without touching production

`restore_database` is destructive by design and meant for a fresh database. To test the round trip safely, point `DATABASE_URL` at a throwaway database (a fresh SQLite file or a scratch Postgres instance) before running `migrate` + `restore_database` — never run it against the live production database "just to check."
