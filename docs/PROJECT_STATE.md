# Platform Project State & Roadmap

This document captures the full project state, architecture, file purposes, and next steps based on the entire `.aider.chat.history.md` conversation and this session.

## 1. Project Goal

We are building a modern sports betting platform with:

- User registration / authentication / password reset
- Single and parlay betting
- Wallet management and crypto deposits/withdrawals
- Real-time odds synchronisation from an external provider
- Automatic match and bet settlement when results arrive
- Affiliate / master commission earnings

The immediate technical focus has been:

- Connect Django to a real odds provider (The Odds API)
- Store sport, tournament, match, and odds data
- Normalise odds with a target margin
- Automatically pull finished match results
- Settle placed bets when results arrive

The front‑end and many advanced features are still in early stages.

---

## 2. Current Technology Stack

- Backend: Django 5.2, Django REST Framework
- Database: SQLite (development default) / PostgreSQL (production when `DATABASE_URL` is set)
- Authentication: SimpleJWT
- Background tasks / cache: Redis + Celery (configured in settings)
- Odds provider: The Odds API

---

## 3. Repository Layout

