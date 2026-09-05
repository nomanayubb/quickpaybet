# QuickPayBet

A modular, multi‑role betting platform with real‑time odds, cryptocurrency payments, and an extensible admin panel.

**Status:** MVP Backend in Development  
**Docs:** See `docs/` for architecture, tech stack, and roadmap.

## Goals
- User bets on live matches with real‑time odds.
- Automatic bet settlement and crypto payment verification.
- Admin/master/agent/user management with configurable bet limits.
- High extensibility for future features (casino, more payment methods, live odds providers, etc.).
- Strict security and auditability.

## Current Backend Capabilities

| Feature | Status |
|---------|--------|
| Custom user model (email login) with roles (user/agent/master/admin) | Implemented |
| JWT authentication (register, login, refresh, `/me`) | Implemented |
| User hierarchy (`parent` field) and betting limits | Implemented |
| Wallet + transaction history + atomic deposit/withdrawal | Implemented (mock crypto flow) |
| Sports, tournaments, matches and odds | Implemented (manual admin/admin API) |
| Bet placement, user bet list | Implemented |
| Bet settlement / refund services | Implemented |
| Admin actions to settle/cancel matches | Implemented |
| Reporting overview & daily report | Implemented |
| Docker + Docker Compose for local development | Implemented |

## Repository Layout
