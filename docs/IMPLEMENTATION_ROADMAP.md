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
- [ ] Password reset & email verification (planned next)

## Phase 3 – Sports / Matches / Odds
- [x] Models for `Sport`, `Tournament`, `Match`
- [x] Public match list & detail API
- [x] Admin/master update of match odds
- [ ] Odds provider adapter & automated feed (later)

## Phase 4 – Wallet & Crypto Payments
- [x] Wallet model with atomic transactions
- [x] Transaction history endpoint
- [x] Deposit / withdraw endpoints (mock immediate flow)
- [ ] Real crypto gateway integration (NOWPayments adapter + webhook)

## Phase 5 – Betting Engine
- [x] `Bet` model (selection, stake, odds, status)
- [x] `place_bet` service with atomic balance reservation
- [x] `My Bets` API
- [ ] Multi‑bet bundles / parlays, cash‑out, custom rules

## Phase 6 – Settlement Automation
- [x] Manual admin actions to settle / cancel matches
- [x] `settle_bets_for_match` service
- [ ] Celery‑based automatic settlement
- [ ] Scheduled job to fetch match results / close odds

## Phase 7 – Admin Panel Custom UI
- [x] Django admin can manage core models
- [ ] Next.js admin dashboard with custom views for matches, odds, users, bets, reports

## Phase 8 – Reports & Analytics
- [x] Basic overview / daily report endpoints (admin/master)
- [ ] Drill‑down by sport, user, match
- [ ] Payout / profit / payment success analytics

## Phase 9 – Security & Hardening
- [ ] Rate limiting
- [ ] Input validation audit
- [ ] OWASP security review
- [ ] XSS/CSRF protection
- [ ] Penetration testing checklist

## Phase 10 – Frontend (Public Site)
- [ ] Next.js scaffold
- [ ] Registration, login, profile
- [ ] Match list and odds display
- [ ] Bet slip and bet placement
- [ ] Wallet deposit / withdraw UI
- [ ] Bet history display

## Phase 11 – Testing & QA
- [ ] Unit tests for services
- [ ] API integration tests
- [ ] Frontend component tests
- [ ] End‑to‑end tests for wallet → bet → settlement flow

## Phase 12 – Deployment
- [ ] Production‑grade Docker Compose
- [ ] Nginx / Let’s Encrypt
- [ ] Postgres backup automation
- [ ] Monitoring (Prometheus / Grafana)

## Continuous Flexibilities
- Payment providers are behind an adapter interface – add new providers later.
- Odds providers are behind an adapter interface – swap feeds later.
- Role permissions can be extended without core changes.
- Each domain is a separate Django app, allowing independent evolution.
