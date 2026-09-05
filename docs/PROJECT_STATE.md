# Platform Project State Overview

Derived from the full `.aider.chat.history.md` and this chat session.

## Project Goal

Build a modern, sport‑betting platform with a Django + DRF backend where users can:

- Register and login (JWT)
- Place single bets on match results (1X2)
- Place parlay bets (multiple selections)
- Manage their wallet balance
- Deposit and withdraw using crypto providers
- Earn / receive affiliate commission on losing bets placed by referred users
- See real sports odds from an external provider
- Auto‑settle matches and bets when results arrive

The current focus has been to wire the **backend odds pipeline** into a functioning solution that the rest of the platform can build on.

---

## Current Technology Stack

| Component | Current Choice |
|-----------|----------------|
| Backend | Django 5.2, Django REST Framework |
| Database | SQLite (development default) / PostgreSQL (when `DATABASE_URL` points to postgres) |
| Cache / Celery Broker | Redis (via `REDIS_URL`) |
| Async Queue | Celery configured in `settings.py` |
| Real‑time odds provider | The Odds API |
| Auth | SimpleJWT |

---

## Repository Layout (Backend)

