# QuickPayBet – Implementation Roadmap (Phased)

## Phase 0 – Documentation Approval
Current status: awaiting `DOCUMENTATION APPROVED – START IMPLEMENTATION`.

## Phase 1 – Repository & Backend Foundation
- Create Django project & apps: `accounts`, `wallet`, `sports`, `bets`, `admin_panel`, `reports`
- Docker + Docker Compose environment
- PostgreSQL connection with migrations
- Base User model (roles: user, agent, master, admin)
- Dummy seed data

## Phase 2 – Authentication & Permissions
- JWT authentication
- Role‑based permissions
- Admin interface (Django Admin at first, custom UI later)
- Password reset, email verification

## Phase 3 – Sports / Matches / Odds
- Model for sports, tournaments, matches, outcomes
- Manual odds creation (admin)
- Odds provider adapter interface (initial implementation can be a fixed odds feed or admin-entered odds)

## Phase 4 – Wallet & Crypto Payments
- Wallet model with atomic transactions
- NOWPayments integration
- Deposit/withdrawal webhooks
- Transaction history

## Phase 5 – Betting Engine
- Betting, stake, and balance reservation
- Multiple bet types (singles/parlay placeholder)
- Close odds for start time

## Phase 6 – Settlement Automation
- Read match result (manually or from feed)
- Celery task to settle bets
- Cron/scheduled job to close/verify bets

## Phase 7 – Admin Panel Custom UI
- Next.js admin dashboard
- Manage matches, odds, users, bets manually
- Audit log viewer
- Reports module

## Phase 8 – Reports & Analytics
- Daily/monthly turnover reports
- PnL by match
- User activity
- Payment success/failure stats

## Phase 9 – Security & Hardening
- Rate limiting
- Input validation across all APIs
- OWASP security checks
- XSS/CSRF protections
- Penetration testing checklist

## Phase 10 – Frontend (Public Site)
- Registration/Login/Profile
- Match list with live odds
- Bet slip and placement
- Transaction history
- Withdrawal flow

## Phase 11 – Testing & QA
- Unit tests for services
- API integration tests
- Frontend component tests
- End‑to‑end tests for key flows

## Phase 12 – Deployment
- Production‑grade Docker Compose
- Nginx/Let’s Encrypt
- Postgres backup automation
- Monitoring with Prometheus/Grafana (free)

## Continuous Flexibilities
- New payment providers added via adapter layer
- New odds providers via adapter layer
- Role permissions can be extended without core changes
- Plugin‑style architecture for future features (casino, live chat, etc.)
