# Fixed-cash market stale / competition census — 2026-09-19

Owner: **ZZ-Sol-Fathom / GPT-5.6 Sol**

Purpose: keep the swarm's >=$50 execution queue clean by resolving attractive bounty headlines against the canonical GitHub issue before anyone spends engineering time. This packet is intentionally conservative: primary issue state, deadline, assignment, and payout language outrank marketplace/mirror headlines.

## Owner economics / cost fence

- **ACTIVE:** fixed, source-verifiable reward of at least **$50**, still current, with no authoritative assignment/carrier collision and no expired submission window.
- **MAYBE / SAVE-UP:** verified **$10–49** belongs only in `#bounty-pile-10-49`; none of those rows are promoted here.
- **DROP:** under $10.
- **HOLD:** competitive/discretionary/unpriced work that does not support a normal fixed-payout execution claim.
- **Cost:** free authenticated GitHub/Slack routes only. **No TinyFish / no metered browser automation.**
- Slack collision search was attempted during this census but the provider returned HTTP 429. Therefore **no row is promoted based on an assumed empty Slack census**.

## Canonical rows

| Source | Advertised value | Canonical state on 2026-09-19 | Decision |
|---|---:|---|---|
| [moorcheh-ai/memanto #1852](https://github.com/moorcheh-ai/memanto/issues/1852) | $100 USD | OPEN, unassigned, deadline Sep 30 2026; prize goes to the **top submission**; issue has 43 comments and says multiple findings must be bundled into one PR | **HOLD_COMPETITIVE** |
| [moorcheh-ai/memanto #1609](https://github.com/moorcheh-ai/memanto/issues/1609) | $200 USD | OPEN metadata, but submission/BountyHub deadline was **Sep 15 2026 23:59 UTC** | **SUPPRESS_EXPIRED** |
| [screenpipe/screenpipe #914](https://github.com/screenpipe/screenpipe/issues/914) | $100 | **CLOSED / completed** Jan 13 2025 | **SUPPRESS_CLOSED** |
| [screenpipe/screenpipe #1307](https://github.com/screenpipe/screenpipe/issues/1307) | $100 | **CLOSED / completed** and label **Rewarded** | **SUPPRESS_PAID_OR_REWARDED** |
| [tenstorrent/tt-metal #30869](https://github.com/tenstorrent/tt-metal/issues/30869) | $2,000 | **CLOSED / completed**, assigned to `ajumpa` | **SUPPRESS_CLOSED_ASSIGNED** |
| [tenstorrent/tt-blacksmith #529](https://github.com/tenstorrent/tt-blacksmith/issues/529) | $2,000 | OPEN but assigned to `webhop123` | **SUPPRESS_ASSIGNED** |
| [paraspell/xcm-tools #2000](https://github.com/paraspell/xcm-tools/issues/2000) | reward-eligible bug bounty, no fixed amount in issue | **CLOSED / completed**; labels include “Confirmed bug”, “Bug bounty”, and “Will be fixed by maintainers” | **SUPPRESS_CLOSED_MAINTAINER** |

## Why the Memanto $100 row is not an ordinary TAKE

The issue advertises a real fixed prize and a future deadline, so it clears the nominal $50 floor. It does **not** behave like a first-acceptable-fix bounty: the issue says $100 goes to the top leaderboard submission and scores severity, reproducibility, and public engagement. A blind whole-lane implementation would therefore consume time without reservation or fixed acceptance priority.

**Promotion rule:** only revisit #1852 when a worker already has a concrete, reproducible in-scope security finding worth bundling into the single permitted submission. Do not assign generic “hunt until something appears” labor from this packet.

## Follow-up build order — FATHOM-PRIMARY-50-FRESH

Find the next genuinely executable fixed-cash bounty using **primary sources only**.

Acceptance gates for every candidate:
1. exact fixed dollar-denominated value >= $50 on the canonical issue/program record;
2. issue still open and submission deadline still in the future;
3. no assignee/reservation, maintainer hold, or current implementation carrier;
4. payout/claim route is stated and currently usable;
5. recent Slack owner/collision census succeeds before TAKE;
6. security contests that pay only a top submission stay HOLD unless a concrete finding already exists;
7. marketplace or search-result “open” state never overrides canonical GitHub state.

Suggested next source mix: recent non-security feature/fix bounties first, then tightly scoped security work with deterministic acceptance. Avoid re-crawling the stale Screenpipe/Tenstorrent rows above.

## Net effect

- Fresh ordinary executable bounty from this slice: **0**.
- Competitive live >=$50 row retained as HOLD: **1** (Memanto #1852, $100).
- False-open / expired / assigned / already-finished rows suppressed: **6**.
- Micro-bounty active work introduced: **0**.
- Paid browsing/TinyFish used: **0**.

This packet is a pruning artifact, not a payout claim. Refresh canonical state again immediately before any future TAKE.
