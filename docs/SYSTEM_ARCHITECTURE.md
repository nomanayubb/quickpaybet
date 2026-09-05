# QuickPayBet – High-Level System Architecture

## Logical Modules
~~~mermaid
graph TD
    User[Browser User] -->|Next.js| Frontend
    Agent/Admin -->|Next.js Admin| FrontendAdmin

    Frontend -->|REST/API| API(Gateway: Django REST)
    FrontendAdmin -->|REST/API| API
    API --> Auth[Accounts & Permissions]
    API --> Matches[Matches & Odds]
    API --> Bets[Betting Engine]
    API --> Wallet[Wallet / Payments]
    API --> Reports[Reporting & Audit]

    Wallet -->|Async Jobs| Celery
    Celery --> Payments[Crypto Gateway Adapter]
    Payments --> NOWPayments[(NOWPayments API)]

    Matches --> OddsIngestor[Odds Poller / WebSocket]
    OddsIngestor --> OddsSource[Third-Party Odds Feed]

    Bets --> Settlement[Settlement Worker]
    Settlement --> Results[Results Service]

    Database[(PostgreSQL)] --> API
    Redis[(Redis)] -->|Cache| API
    Redis[(Redis)] -->|Broker| Celery
~~~

## Main Flows

**Deposit**  
1. User selects crypto coin.  
2. API creates a NOWPayments invoice.  
3. Celery worker polls or uses webhook to confirm payment.  
4. Wallet balance credited atomically.

**Bet Placement**  
1. User locks in odds and stake.  
2. Funds are reserved (or debited) in a transactional block.  
3. Bet stored with status `PENDING`.  

**Settlement**  
1. Celery sees match result.  
2. Determines winning bets.  
3. Updates balances and sets bet status `WON` or `LOST`.  
4. Every action logged in audit trail.

**Admin Overrides**  
Admin can manually adjust odds / result / payout before final settlement.  
All manual actions require permission and are logged.

## Data Flow Highlights
- All monetary operations are wrapped in `transaction.atomic()`.  
- Balance is checked and updated atomically to prevent race conditions.  
- External service calls are always async via Celery, never in request path.  
- Audit table stores every change with actor, timestamp, and reason.
