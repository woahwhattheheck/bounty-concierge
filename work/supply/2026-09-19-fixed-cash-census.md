# Fixed-Cash Supply Census — 2026-09-19

Owner: **ZZ-Solstice-Palisade / GPT-5.6 Sol**

Purpose: keep the swarm from confusing issue difficulty, campaign labels, points, or “Maybe Rewarded” status with guaranteed cash. This packet applies the current operator floor literally:

- **active new work:** fixed/verified USD cash **>= $50**
- **$10–49:** pile separately; do not spend an active implementation seat
- **unpriced / “Maybe Rewarded”:** **HOLD_UNPRICED**
- **claim-, account-, CLA-, assignment-, or approval-gated fixed cash:** **GATED_FIXED_CASH** until the gate is satisfied
- **stale/collision-heavy:** do not implement blindly

This is a discovery/economics packet, **not** a statement of entitlement or payment approval.

## Source fence

Captured 2026-09-19 from:

1. Midas Developer OS public bounty board: https://www.midas.ceo/developers
2. Canonical Midas repository bounty board: https://github.com/bujiproject-art/agentmidasopendevteam/blob/main/BOUNTIES.md
3. Canonical Midas repository README: https://github.com/bujiproject-art/agentmidasopendevteam
4. GitHub issue search for OPEN issues carrying all three GrantFox/FWC26 labels:
   `GrantFox OSS`, `Maybe Rewarded`, `Official Campaign | FWC26`

A GitHub secondary rate limit prevented a reliable live claim-issue census for the external Midas repo during this capture. Therefore every Midas row below is **GATED_FIXED_CASH / CLAIM-CENSUS REQUIRED**, not READY.

---

## A. Midas fixed-cash board — 41 app bounties

The canonical board advertises cash amounts and says payout is via Stripe after approval. The repository workflow requires picking a bounty, checking/opening a claim issue, a fork/branch, PR, security review, and approval; the README additionally requires free signup and CLA before the first PR.

**Do not implement until the current claim/issue state is refreshed and account/CLA eligibility is confirmed.**

| # | Bounty | USD | Difficulty | State here |
|---:|---|---:|---|---|
| 1 | Contact Management Dashboard | 150 | Intermediate | GATED_FIXED_CASH |
| 2 | Deal Pipeline Board | 200 | Intermediate | GATED_FIXED_CASH |
| 3 | Email Campaign Builder | 300 | Advanced | GATED_FIXED_CASH |
| 4 | Lead Scoring Engine | 250 | Advanced | GATED_FIXED_CASH |
| 5 | Sales Forecasting Widget | 200 | Intermediate | GATED_FIXED_CASH |
| 6 | Meeting Scheduler | 100 | Beginner | GATED_FIXED_CASH |
| 7 | Blog Publishing System | 200 | Intermediate | GATED_FIXED_CASH |
| 8 | Social Media Scheduler | 300 | Advanced | GATED_FIXED_CASH |
| 9 | Podcast Manager | 200 | Intermediate | GATED_FIXED_CASH |
| 10 | Video Content Library | 150 | Intermediate | GATED_FIXED_CASH |
| 11 | Content Calendar | 100 | Beginner | GATED_FIXED_CASH |
| 12 | Affiliate Dashboard Pro | 500 | Critical | GATED_FIXED_CASH |
| 13 | Referral Link Manager | 200 | Intermediate | GATED_FIXED_CASH |
| 14 | Commission Calculator Widget | 150 | Beginner | GATED_FIXED_CASH |
| 15 | Team Genealogy Tree | 400 | Advanced | GATED_FIXED_CASH |
| 16 | Invoice Generator | 300 | Advanced | GATED_FIXED_CASH |
| 17 | Expense Tracker | 200 | Intermediate | GATED_FIXED_CASH |
| 18 | Revenue Dashboard | 500 | Critical | GATED_FIXED_CASH |
| 19 | Payment Gateway Manager | 400 | Advanced | GATED_FIXED_CASH |
| 20 | Ticket System | 250 | Intermediate | GATED_FIXED_CASH |
| 21 | Knowledge Base Builder | 300 | Advanced | GATED_FIXED_CASH |
| 22 | Live Chat Widget | 350 | Advanced | GATED_FIXED_CASH |
| 23 | FAQ Manager | 100 | Beginner | GATED_FIXED_CASH |
| 24 | Funnel Visualizer | 250 | Intermediate | GATED_FIXED_CASH |
| 25 | A/B Testing Framework | 300 | Advanced | GATED_FIXED_CASH |
| 26 | User Session Replay | 200 | Intermediate | GATED_FIXED_CASH |
| 27 | Employee Directory | 150 | Beginner | GATED_FIXED_CASH |
| 28 | Leave Management | 200 | Intermediate | GATED_FIXED_CASH |
| 29 | Onboarding Checklist | 100 | Beginner | GATED_FIXED_CASH |
| 30 | Restaurant POS Interface | 400 | Advanced | GATED_FIXED_CASH |
| 31 | Real Estate Listing Portal | 350 | Advanced | GATED_FIXED_CASH |
| 32 | Fitness Class Scheduler | 200 | Intermediate | GATED_FIXED_CASH |
| 33 | Law Firm Case Manager | 400 | Advanced | GATED_FIXED_CASH |
| 34 | Healthcare Appointment System | 500 | Critical | GATED_FIXED_CASH |
| 35 | Spanish Language Pack | 100 | Beginner | GATED_FIXED_CASH |
| 36 | French Language Pack | 100 | Beginner | GATED_FIXED_CASH |
| 37 | Portuguese Language Pack | 100 | Beginner | GATED_FIXED_CASH |
| 38 | i18n Framework Setup | 250 | Intermediate | GATED_FIXED_CASH |
| 39 | Landing Page Template Kit | 200 | Intermediate | GATED_FIXED_CASH |
| 40 | Icon & Illustration Pack | 150 | Beginner | GATED_FIXED_CASH |
| 41 | Video Tutorial Series | 300 | Intermediate | GATED_FIXED_CASH |

### Midas priority order after a fresh claim census

This is **not** an entitlement ranking. It is a throughput order for a future authenticated/eligible seat after collision checks:

1. **#18 Revenue Dashboard — $500**
2. **#12 Affiliate Dashboard Pro — $500**
3. **#34 Healthcare Appointment System — $500**
4. **#15 Team Genealogy Tree — $400**
5. **#19 Payment Gateway Manager — $400**
6. **#30 Restaurant POS Interface — $400**
7. **#33 Law Firm Case Manager — $400**
8. **#22 Live Chat Widget — $350**
9. **#31 Real Estate Listing Portal — $350**
10. then $300 lanes, then $250, $200, $150, $100

The board says a claimed bounty has a 14-day PR window before returning to the pool. Refresh current issue/claim state immediately before any TAKE.

### Midas OpenClaw expert board

The developer page separately advertises:

| Bounty | USD | State here |
|---|---:|---|
| OpenClaw Migration Lead | 500 | GATED_FIXED_CASH |
| OpenClaw Plugin Architecture | 400 | GATED_FIXED_CASH |
| OpenClaw Auth Bridge | 300 | GATED_FIXED_CASH |
| OpenClaw Data Sync | 350 | GATED_FIXED_CASH |

These are application/approval gated. Do not treat the page’s “Apply” affordance as an assignment.

---

## B. GrantFox/FWC26 substantial OPEN pool — economics HOLD

The GitHub connector returned the following as OPEN under the full three-label GrantFox/FWC26 query during this capture. Their issue bodies say work **may be rewarded**; no fixed USD amount was established by this census.

Therefore every row is **HOLD_UNPRICED** under the current $50 owner floor.

| Repository / issue | Scope | Economic state |
|---|---|---|
| AgriTrust-Protocol/AgriTrust-Contracts#150 | decentralized dispute resolution + jury/appeal/escrow contracts | HOLD_UNPRICED |
| StableRoute-Org/Stableroute-backend#551 | per-tenant/API-key sliding-window rate limiter | HOLD_UNPRICED |
| Agentpay-Org/Agentpay-contracts#449 | typed settlement error taxonomy + exhaustive negative tests | HOLD_UNPRICED |
| Agentpay-Org/Agentpay-contracts#451 | bounded admin parameter setter + auth/events/view | HOLD_UNPRICED |
| Agentpay-Org/Agentpay-frontend#628 | schema validation + accessible inline/live-region errors | HOLD_UNPRICED |
| Agentpay-Org/Agentpay-frontend#629 | debounced search + cancellation/stale response guard | HOLD_UNPRICED |
| Liquifact/Liquifact-contracts#1199 | namespaced escrow storage keys + TTL/bump management | HOLD_UNPRICED |
| QuickLendX/quicklendx-frontend#62 | date-range boundary validation | HOLD_UNPRICED |
| QuickLendX/quicklendx-frontend#31 | Stellar G-address payout validation | HOLD_UNPRICED |
| aid-linkk/aidlink-frontend#30 | shared API error handling utilities | HOLD_UNPRICED |
| aid-linkk/aidlink-frontend#33 | currency input masks + validation | HOLD_UNPRICED |
| aid-linkk/aidlink-frontend#27 | lazy-load analytics components | HOLD_UNPRICED |
| AnchorKit-1/Anchorkit-1#60 | request correlation IDs through transport/logging | HOLD_UNPRICED |
| Flux-DeFi/LiquidFlow#163 | stream storage TTL/rent bump strategy | HOLD_UNPRICED |
| Gryd-lock/grydlock-testkit#24 | interactive atomic fixture-authoring CLI | HOLD_UNPRICED |
| Gryd-lock/grydlock-testkit#11 | multi-operation Stellar XDR fixtures | HOLD_UNPRICED |
| Gryd-lock/grydlock-testkit#10 | fixture-distribution health report | HOLD_UNPRICED |

### GrantFox rule for this swarm

Do **not** infer dollars from:
- issue complexity,
- campaign labels,
- point values,
- “Maybe Rewarded,”
- historical payouts on other issues,
- or a merged PR.

Only a current source that establishes a concrete payout >= $50 can promote one of these rows into the active cash queue. Refresh issue, comments/assignment, matching open PRs, and reward terms together before promotion.

---

## C. Collision and evidence protocol

Before any future implementation seat starts one of these rows:

1. **Economics:** bind exact advertised fixed amount and payout provider/terms.
2. **Claim gate:** verify required signup, CLA, application, assignment, claim comment, or wallet state.
3. **Collision:** inspect issue comments, assignees, linked development, and all matching open PRs.
4. **Source freshness:** pin current default-branch SHA and confirm the acceptance gap still exists.
5. **Repo viability:** reject archived/read-only repos and stale issues already satisfied by main.
6. **Custody:** one implementation owner; peer lanes should be review, source audit, or validation—not duplicate builds.
7. **Collection:** merged code is not automatically a payment award. Keep payout/appeal evidence separate from implementation evidence.
8. **Free route:** use existing authenticated connectors/local tools; do not launch paid browser automation to satisfy a claim gate.

## D. Next operator actions

- **Midas:** a free authenticated seat should first do a claim census for #18/#12/#34/#15/#19/#30/#33/#22/#31, then claim **at most one** genuinely unclaimed lane through the provider’s required process before source work.
- **GrantFox:** keep the OPEN pool parked until a fixed >=$50 reward is independently established. If economics is later confirmed, re-run collision + source freshness immediately.
- **Do not downgrade the owner floor just to keep seats busy.** A HOLD with clean evidence is better than spending hours on a discretionary or already-claimed task.

