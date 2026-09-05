# QuickPayBet – Implementation Roadmap (Phased)

## Phase 0 – Documentation Approval
- [x] Approved by client  
- [x] Initial architecture, technology stack, and roadmap committed

## Phase 1 – Repository & Backend Foundation
- [x] Django project & core apps created (`accounts`, `wallet`, `sports`, `bets`, `reports`)
- [x] Docker + Docker Compose environment configured
- [x] PostgreSQL, Redis, Celery wired in settings
- [x] Base `User` model with roles (`user`, `agent`, `master`, `admin`)
- [x] Initial database migrations for accounts, wallet, sports, bets

## Phase 2 – Authentication & Permissions
- [x] JWT authentication (register, login, refresh)
- [x] `MeView` returning current user
- [x] Role‑based permissions for admin/master endpoints
- [x] `parent` hierarchy and per‑user betting limits
- [x] Password reset token flow (API), without email delivery

## Phase 3 – Sports / Matches / Odds
- [x] Models for `Sport`, `Tournament`, `Match`
- [x] Public match list & detail API
- [x] Admin/master update of match odds
- [x] Odds provider adapter (mock)
- [x] Management command `sync_odds` for manual feed retrieval

## Phase 4 – Wallet & Crypto Payments
- [x] Wallet model with atomic transactions
- [x] Transaction history endpoint
- [x] Deposit / withdraw endpoints
- [x] Pending payment record + mock provider webhook (credit after confirmation)
- [ ] Real crypto gateway integration (NOWPayments adapter)

## Phase 5 – Betting Engine
- [x] `Bet` model (selection, stake, odds, status)
- [x] `place_bet` service with atomic balance reservation
- [x] `My Bets` API
- [ ] Multi‑bet bundles / parlays, cash‑out, custom rules

## Phase 6 – Settlement Automation
- [x] Manual admin actions to settle / cancel matches
- [x] `settle_bets_for_match` service
- [x] Celery task to settle finished matches
- [x] Celery task to mark scheduled matches as `LIVE`
- [ ] Scheduled job to fetch match results from real provider

## Phase 7 – Admin Panel Custom UI
- [x] Django admin can manage core models
- [ ] Next‑level custom dashboard (could remain Django admin or be replaced by a React app)

## Phase 8 – Reports & Analytics
- [x] Basic overview / daily report endpoints (admin/master)
- [ ] Drill‑down by sport, user, match
- [ ] Payout / profit / payment success analytics

## Phase 9 – Security & Hardening
- [ ] Rate limiting
- [ ] Input validation audit
- [ ] OWASP security review
- [ ] XSS/CSRF protection (already enabled by Django defaults)
- [ ] Penetration testing checklist

## Phase 10 – Frontend
- [x] Django template public UI (registration, matches, bet slip, wallet, bet history)
- [ ] Optional: replace or supplement with React/Next.js in the future

## Phase 11 – Testing & QA
- [ ] Unit tests for services
- [ ] API integration tests
- [ ] Frontend component tests (if React is introduced)
- [ ] End‑to‑end tests for wallet → bet → settlement flow

## Phase 12 – Deployment
- [ ] Production‑grade Docker Compose
- [ ] Nginx / Let’s Encrypt
- [ ] Postgres backup automation
- [ ] Monitoring (Prometheus / Grafana)

## Continuous Flexibilities
- Payment providers are behind an adapter interface – add new providers later.
- Odds providers are behind an adapter interface – swap feeds later.
- The Django template UI can be replaced by a Next.js or other SPA later without breaking the API/services.
- Role permissions can be extended without core changes.
- Each domain is a separate Django app, allowing independent evolution.
