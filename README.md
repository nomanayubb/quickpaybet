# QuickPayBet

A modular, multi‑role betting platform with real‑time odds, cryptocurrency payments, and an extensible Django admin panel.

**Status:** MVP Backend + Django Template UI  
**Docs:** See `docs/` for architecture, tech stack, and roadmap.

## Goals
- User bets on live matches with real‑time odds.
- Automatic bet settlement and crypto payment verification.
- Admin/master/agent/user management with configurable bet limits and commissions.
- High extensibility for future features (casino, more payment methods, live odds providers, etc.).
- Strict security and auditability.

## Current Capabilities

| Feature | Status |
|---------|--------|
| Custom user model (email login) with roles (user/agent/master/admin) | Implemented |
| Session login/registration for HTML UI | Implemented |
| JWT authentication (register, login, refresh) for API | Implemented |
| User hierarchy (`parent`) and betting limits | Implemented |
| Commission rate per user for affiliates | Implemented |
| Wallet + transaction history + atomic deposit/withdrawal | Implemented (mock crypto flow + webhook confirmation) |
| Sports, tournaments, matches and odds | Implemented |
| Odds provider adapter (mock / skeleton for real) | Added |
| Match sync from provider (`manage.py sync_odds`) | Added |
| Auto‑mark matches `LIVE` when start time passes | Added (Celery task) |
| Bet placement (web form + API) | Implemented |
| Parlay (multi‑selection) betting | Implemented |
| Bet settlement / refund for singles and parlays | Implemented |
| Auto‑settlement Celery task | Implemented |
| Affiliate commission on losing bets | Implemented |
| Admin actions to settle/cancel matches | Implemented |
| Admin pages for users, sports, tournaments, matches, reports, audit | Implemented |
| Reporting overview & daily report | Implemented |
| Password reset API + web flow | Implemented (dev email console) |
| Docker + Docker Compose for local development | Implemented |

## Repository Layout

