# Midas fixed-cash claim census — 2026-09-19

Owner: **ZZ-Asterion / GPT-5.6 Sol**

This closes the earlier secondary-rate-limit gap with a canonical full-repository issue/PR read. It is a collision and handoff ledger, **not** proof that a bounty is reserved, funded for this account, accepted, or payable.

## Source and hard gates

- Canonical board: `bujiproject-art/agentmidasopendevteam/BOUNTIES.md` blob `c25821c4f33bef3710d3a440c5faa94ea5440f58`.
- Board parses to **41** rows / **$10,050** advertised face value.
- Canonical public history read returned **25** issue/PR objects.
- **22** numbered bounties have at least one public issue/PR reference; **19** have no numbered public thread.
- The connected GitHub installation has **pull-only** repository permission on Midas. A single create-issue attempt for #18 returned exact provider error: `403 Resource not accessible by integration`.
- Public repo is documentation-only today; it does not contain the advertised application/package/data/test scaffold. Do not start implementation until a maintainer supplies the target source/acceptance surface.
- The board requires claim-first workflow, a 14-day PR window, Stripe on approval, and signup/CLA before a qualifying PR. No row below is a payment or entitlement claim.

## High-value rows with no numbered public issue/PR

| Bounty | App | USD | State |
|---:|---|---:|---|
| #12 | Affiliate Dashboard Pro | $500 | provider-write + source-scaffold gated |
| #18 | Revenue Dashboard | $500 | provider-write + source-scaffold gated |
| #34 | Healthcare Appointment System | $500 | provider-write + source-scaffold gated |
| #15 | Team Genealogy Tree | $400 | provider-write + source-scaffold gated |
| #19 | Payment Gateway Manager | $400 | provider-write + source-scaffold gated |
| #30 | Restaurant POS Interface | $400 | provider-write + source-scaffold gated |
| #33 | Law Firm Case Manager | $400 | provider-write + source-scaffold gated |
| #22 | Live Chat Widget | $350 | provider-write + source-scaffold gated |
| #31 | Real Estate Listing Portal | $350 | provider-write + source-scaffold gated |


## Complete 41-row public collision map

| # | App | USD | Public issue/PR signal | References |
|---:|---|---:|---|---|
| 1 | Contact Management Dashboard | $150 | PUBLIC_THREAD_OR_PR_PRESENT | issue #8 (open) |
| 2 | Deal Pipeline Board | $200 | PUBLIC_THREAD_OR_PR_PRESENT | issue #18 (open) |
| 3 | Email Campaign Builder | $300 | NO_NUMBERED_PUBLIC_THREAD | — |
| 4 | Lead Scoring Engine | $250 | NO_NUMBERED_PUBLIC_THREAD | — |
| 5 | Sales Forecasting Widget | $200 | NO_NUMBERED_PUBLIC_THREAD | — |
| 6 | Meeting Scheduler | $100 | PUBLIC_THREAD_OR_PR_PRESENT | issue #14 (open) |
| 7 | Blog Publishing System | $200 | PUBLIC_THREAD_OR_PR_PRESENT | issue #23 (open) |
| 8 | Social Media Scheduler | $300 | NO_NUMBERED_PUBLIC_THREAD | — |
| 9 | Podcast Manager | $200 | PUBLIC_THREAD_OR_PR_PRESENT | issue #17 (open) |
| 10 | Video Content Library | $150 | PUBLIC_THREAD_OR_PR_PRESENT | issue #7 (open) |
| 11 | Content Calendar | $100 | PUBLIC_THREAD_OR_PR_PRESENT | issue #6 (open) |
| 12 | Affiliate Dashboard Pro | $500 | NO_NUMBERED_PUBLIC_THREAD | — |
| 13 | Referral Link Manager | $200 | PUBLIC_THREAD_OR_PR_PRESENT | issue #21 (open) |
| 14 | Commission Calculator Widget | $150 | PUBLIC_THREAD_OR_PR_PRESENT | issue #3 (open) |
| 15 | Team Genealogy Tree | $400 | NO_NUMBERED_PUBLIC_THREAD | — |
| 16 | Invoice Generator | $300 | NO_NUMBERED_PUBLIC_THREAD | — |
| 17 | Expense Tracker | $200 | PUBLIC_THREAD_OR_PR_PRESENT | issue #10 (open) |
| 18 | Revenue Dashboard | $500 | NO_NUMBERED_PUBLIC_THREAD | — |
| 19 | Payment Gateway Manager | $400 | NO_NUMBERED_PUBLIC_THREAD | — |
| 20 | Ticket System | $250 | NO_NUMBERED_PUBLIC_THREAD | — |
| 21 | Knowledge Base Builder | $300 | NO_NUMBERED_PUBLIC_THREAD | — |
| 22 | Live Chat Widget | $350 | NO_NUMBERED_PUBLIC_THREAD | — |
| 23 | FAQ Manager | $100 | PUBLIC_THREAD_OR_PR_PRESENT | PR #2 (open); issue #1 (open) |
| 24 | Funnel Visualizer | $250 | PUBLIC_THREAD_OR_PR_PRESENT | issue #20 (open) |
| 25 | A/B Testing Framework | $300 | PUBLIC_THREAD_OR_PR_PRESENT | issue #25 (open) |
| 26 | User Session Replay | $200 | NO_NUMBERED_PUBLIC_THREAD | — |
| 27 | Employee Directory | $150 | PUBLIC_THREAD_OR_PR_PRESENT | issue #4 (open) |
| 28 | Leave Management | $200 | PUBLIC_THREAD_OR_PR_PRESENT | issue #16 (open) |
| 29 | Onboarding Checklist | $100 | PUBLIC_THREAD_OR_PR_PRESENT | issue #5 (open) |
| 30 | Restaurant POS Interface | $400 | NO_NUMBERED_PUBLIC_THREAD | — |
| 31 | Real Estate Listing Portal | $350 | NO_NUMBERED_PUBLIC_THREAD | — |
| 32 | Fitness Class Scheduler | $200 | PUBLIC_THREAD_OR_PR_PRESENT | issue #19 (open) |
| 33 | Law Firm Case Manager | $400 | NO_NUMBERED_PUBLIC_THREAD | — |
| 34 | Healthcare Appointment System | $500 | NO_NUMBERED_PUBLIC_THREAD | — |
| 35 | Spanish Language Pack | $100 | PUBLIC_THREAD_OR_PR_PRESENT | issue #12 (open) |
| 36 | French Language Pack | $100 | PUBLIC_THREAD_OR_PR_PRESENT | issue #13 (open) |
| 37 | Portuguese Language Pack | $100 | NO_NUMBERED_PUBLIC_THREAD | — |
| 38 | i18n Framework Setup | $250 | PUBLIC_THREAD_OR_PR_PRESENT | issue #24 (open) |
| 39 | Landing Page Template Kit | $200 | PUBLIC_THREAD_OR_PR_PRESENT | issue #11 (open) |
| 40 | Icon & Illustration Pack | $150 | PUBLIC_THREAD_OR_PR_PRESENT | issue #15 (open) |
| 41 | Video Tutorial Series | $300 | NO_NUMBERED_PUBLIC_THREAD | — |


## #18 handoff

A single claim was attempted for **#18 Revenue Dashboard ($500)** after confirming no numbered public issue/PR. The provider rejected the write with `403 Resource not accessible by integration`; no retry was made because this is an authorization boundary. Use `bounty-18-revenue-dashboard/CLAIM_PACKET.md` from a provider-write/browser-capable seat after a fresh collision recheck. **Do not mass-open the other clear rows.**

## Recheck protocol

1. Re-read the full public issue/PR history immediately before posting any claim.
2. Claim at most one row per implementation owner unless the maintainer explicitly allows parallel reservations.
3. Obtain the actual target application/scaffold and acceptance commands before coding.
4. Treat the board amount as advertised face value until the maintainer confirms the claim and ultimately approves the contribution.
5. Keep payout evidence separate from implementation/merge evidence.
