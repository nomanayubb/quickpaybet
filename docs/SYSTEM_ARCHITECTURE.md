# QuickPayBet – High-Level System Architecture

## Logical Modules
~~~mermaid
graph TD
    User[Browser User] -->|HTTP| DjangoTemplates
    Agent/Admin -->|HTTP| DjangoAdmin

    DjangoTemplates -->|Server Rendering| Django
    DjangoAdmin -->|Django Admin| Django

    Django --> Auth[Accounts & Permissions]
    Django --> Matches[Matches & Odds]
    Django --> Bets[Betting Engine]
    Django --> Wallet[Wallet / Payments]
    Django --> Reports[Reporting & Audit]

    Wallet -->|Async Jobs| Celery
    Celery --> Payments[Crypto Gateway Adapter]
    Payments --> NOWPayments[(NOWPayments API)]

    Matches --> OddsIngestor[Odds Poller / WebSocket]
    OddsIngestor --> OddsSource[Third-Party Odds Feed]

    Bets --> Settlement[Settlement Worker]
    Settlement --> Results[Results Service]

    Database[(PostgreSQL)] --> Django
    Redis[(Redis)] -->|Cache| Django
    Redis[(Redis)] -->|Broker| Celery
~~~

## Main Flows

**Deposit**  
1. User selects crypto coin.  
2. Server stores a `CryptoPayment` record in `PENDING` status.  
3. User sees a deposit address (mock or real).  
4. Provider webhook confirms payment.  
5. `deposit_funds` credits wallet in a database transaction.

**Bet Placement**  
1. User selects outcome and stake.  
2. Funds are reserved (subtracted) in a transaction.  
3. `Bet` record created with `PENDING` status.  
4. During settlement, wallets are updated automatically.

**Settlement**  
1. Celery sees a finished match with scores.  
2. Determines winning bets.  
3. Updates balances and sets bet status `WON` or `LOST`.  
4. Every action is logged in audit trail.

**Admin Overrides**  
Admin can manually adjust odds / result / payout before final settlement.  
All manual actions happen in Django admin and are logged.

## Environment Components
- **Django** – serves both the public (`web/apps/web/views.py`) and the API (`api/` endpoints).  
- **Redis** – used as Celery broker and for caching.  
- **PostgreSQL** – stores all persistent data.  
- **Celery** – runs settlement and match status tasks.

## API and UI Together
- Public HTML pages are rendered by Django templates.  
- An internal REST API is exposed for mobile clients and future SPA frontends.  
- Same core services (`services.py`) are used by both the templates and the API.
