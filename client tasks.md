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
- [x] Provide casino/iGaming aggregator API access — **received 2026-09-08**: SoftAPI (igamingapis.live, migrating to world-casino-api.com) TOKEN + SECRET, added to `.env`. Full integration built (`apps/casino`). **Confirmed live-tested and NOT usable for this platform**: the only provider active on this account (WM Casino) geo-blocks Pakistan entirely at the game level (confirmed by opening a real launch URL), and igamingapis support has gone unresponsive since the block was raised with them. Code stays in the repo (`SoftAPIProvider`) in case the account situation changes later, but the active provider was switched to Waija instead (see below).
- [x] **Switched active casino provider to Waija (2026-09-09)** — a different aggregator (`documentation.waija.com`, underlying product is actually "SlotsGateway" per its own PHP client package name), confirmed by you to work for Pakistan with no geo-block, with a real games catalog. Built as a second provider (`WaijaProvider`) behind the same generic interface, replacing SoftAPI as the active one. **Structurally different from SoftAPI**: Waija is a true seamless wallet - no balance is ever handed over at launch; every bet and win is applied to the real wallet live, the instant it happens, via signed callbacks. This is closer to full automation than SoftAPI's model.
- [ ] **Provide real Waija credentials** to go live: `WAIJA_API_LOGIN`, `WAIJA_API_PASSWORD`, and your account's `WAIJA_API_URL` (from Waija's Settings page), plus a `WAIJA_SALTKEY` (also from Settings - used to verify their wallet callbacks are genuine). Add all four to `.env`, then set `CASINO_PROVIDER=waija`.
- [ ] In the Waija backoffice, set your **Callback URL** to `https://<your-domain>/api/casino/waija/callback/`, and whitelist your production server's IP (same requirement as SoftAPI had - real launches will reject a non-whitelisted IP).
- [ ] Confirm Waija's actual pricing (GGR rate and whether there are any other fees) before going live with real money - message sent to them 2026-09-09, awaiting reply.
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
