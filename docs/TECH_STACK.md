# QuickPayBet – Technology Stack (Decision Record)

| Layer | Choice | Reason |
|-------|--------|--------|
| Backend | Python 3.12 + Django 5.2 | Mature, secure ORM, strong community |
| API | Django REST Framework | Rapid building of REST API |
| Database | PostgreSQL 16 | Free, ACID, powerful for financial data |
| Cache / Message Queue | Redis 7 | Used for caching and Celery broker |
| Background Tasks | Celery 5 | Async payment/settlement/report generation |
| Frontend | Next.js 14 (React) | Flexible, SEO‑friendly, easy future expansion |
| Mobile/Client | API‑first – future native apps can use same API |
| Payment integration | NOWPayments (free, no monthly fee) – crypto |
| Odds provider | Open‑source / free feed initially (e.g., The Odds API free tier or a custom manual admin feed to start) |
| Deployment | Docker + Docker Compose | Develop locally, scale later |

## Cost Logic
All selected technologies are free, open‑source, or have generous free tiers.  
We avoid paid services until the platform is ready to handle real revenue.

## Flexibility
- Payment providers are behind an interface so we can add new ones (bank cards, UPI, etc.) later.
- Odds providers are behind adapters so third‑party feeds can be swapped.
- Admin can override any odds manually if needed.
- Each major module (users, wallet, matches, bets, reports) is a separate Django app, permitting independent evolution.
