# Client Tasks

This file tracks action items that require input or approval from the client to complete the QuickPayBet platform.

## How to use
- Check off items as they are completed.
- Add new items as they come up.
- The developer will update this file alongside progress.

## Open Active Tasks

- [x] Provide NOWPayments live API key — **received 2026-09-06, verified working**: real deposit creation tested end-to-end and confirmed live (creates a real deposit address).
- [x] Provide NOWPayments **IPN secret** — **received and verified working 2026-09-06**. Also caught and fixed a real bug while testing this: the webhook signature check was hashing the request body in the wrong format (NOWPayments requires the JSON re-sorted before hashing) and would have silently rejected every real deposit confirmation. Fixed and confirmed working with a realistic simulated webhook.
- [x] **Manual payout admin tool built (2026-09-06)** — since NOWPayments requires a 2FA code for every single payout batch (their security design, not something to build around), full hands-off automation was never going to be possible. Instead: `/panel/withdrawals/` lists pending withdrawal requests, and clicking "Process" walks through two steps — (1) enter your NOWPayments email+password directly into that page (goes straight to NOWPayments, never stored, never seen by Claude), which creates the payout batch, then (2) enter the 6-digit 2FA code from your authenticator app to confirm it. **Not yet tested with a real payout** — that needs you to actually do it once, ideally with a small test amount first. Do that whenever you're ready and let me know how it goes (a UI hiccup vs. an actual money problem are both useful to know).
- [ ] Provide The Odds API key and select sport key(s)
- [ ] Provide production SMTP credentials for email delivery
- [ ] Provide domain name and hosting/VPS details
- [ ] Configure DNS and SSL certificate (or confirm Let's Encrypt is acceptable)
- [ ] Provide legal jurisdiction / licensing / compliance requirements
- [ ] Provide asset for platform name, logo, and brand colors
- [ ] Provide legal texts: Terms and Conditions, Privacy Policy, Responsible Gaming policy
- [ ] Provide KYC/verification requirements and policy
- [ ] Configure desired currency display and odds format (decimal, fractional, American)
- [ ] Configure default betting limits and commission rates per master/agent
- [ ] Specify which sports/leagues must be available
- [ ] Confirm whether user should see all sports or only configured ones
- [ ] Provide a demo list of matches / test results, or confirm using the seed data
- [ ] Specify admin email addresses for alerts / audit notifications
- [ ] Provide backup retention policy
- [ ] Decide whether you want a public API for third parties
- [ ] Decide whether you need a mobile app or PWA later
- [ ] Provide mobile‑friendly design requirements (or accept current responsive design)
- [ ] Provide multi‑language acceptance criteria (English/Hindi/Urdu)
- [ ] Define responsible gambling limits (deposit limits, self‑exclusion)
- [ ] Provide any regulatory reporting requirements

## What remains on the developer side (not client tasks)

These will be completed automatically without client input, and are listed only for reference:
- Real crypto adapter hardening
- Automatic result ingestion from a live feed
- Security hardening (2FA, IP‑based abuse detection)
- Monitoring, logging, deployment automation
- Advanced reporting exports
- Optional cash‑out and more bet types

## Progress log

(Will be updated as tasks are completed.)
