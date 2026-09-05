# QuickPayBet – Project Overview

## Business Idea
A crypto‑native betting platform where users can place bets on sports events using real‑time odds, with fully automated result settlement and payment verification.

## Key User Roles
- **Guest** – can view public pages
- **User (Player)** – can deposit, place bets, withdraw, view history
- **Master** – can create agents/users, configure limits, manage affiliate structures
- **Agent** – can manage a subset of users, create sub‑users
- **Admin** – full control over odds, matches, payments, limits, reports

## Core Features (MVP)
1. User registration/authentication
2. Crypto deposits and withdrawals (automated verification)
3. Sports/matches management
4. Real‑time odds ingestion from selected providers
5. Placing single/multiple bets
6. Automatic bet settlement when match result is known
7. User balance updates after settlement
8. Admin panel to manage everything

## Architectural Non‑Negotiables
- Modular code with separate Django apps per domain.
- All business logic in service layer, not in views.
- Every transaction uses database atomic operations.
- Schedule tasks with Celery for settlement/payment processing.
- Provide full REST API for future frontend/mobile clients.
- Extensive audit logs for every financial/configuration action.
