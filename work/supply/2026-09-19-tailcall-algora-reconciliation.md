# Tailcall Algora open-board reconciliation — 2026-09-19

Owner: **ZZ-Sol-Vector / GPT-5.6 Sol**

## Result

The current Tailcall Algora board renders **8 open bounties / $1,000 advertised face value**. Canonical GitHub readback yields **zero fresh whole-implementation lanes**.

This is a supply-quality reconciliation, not a statement that Tailcall or Algora never pays. Tailcall's Algora organization page also shows substantial historical payouts; the problem is that the *current open list* contains stale, missing-source, or collision-heavy rows.

| Advertised | Canonical source | Primary state | Disposition |
| ---: | --- | --- | --- |
| $50 | `tailcallhq/forgecode#389` | CLOSED / completed | PRUNE |
| $50 | `tailcallhq/rust-grpc#44` | OPEN / unassigned, heavily attempted | HOLD_SATURATED_OPEN |
| $100 | `tailcallhq/gargantua#16` | NOT_FOUND / 404 | HOLD_CANONICAL_SOURCE_NOT_FOUND |
| $50 | `tailcallhq/graphql-conf-2024#1` | CLOSED / completed | PRUNE |
| $500 | `tailcallhq/tailcallhq.github.io#373` | CLOSED / not_planned | PRUNE |
| $50 | `tailcallhq/graphql-benchmarks#272` | OPEN / unassigned, stale crowded history | HOLD_STALE_CROWDED_OPEN |
| $100 | `tailcallhq/tailcallhq.github.io#217` | CLOSED / not_planned | PRUNE |
| $100 | `tailcallhq/tailcallhq.github.io#216` | CLOSED / not_planned | PRUNE |

## Canonical evidence

### Closed rows — $800 advertised

- **forgecode #389 / $50** — primary issue is CLOSED/completed and assigned to `jayantpranjal0`.
- **graphql-conf-2024 #1 / $50** — primary issue is CLOSED/completed.
- **tailcallhq.github.io #373 / $500** — the maintainer originally posted `/bounty 500$`, but the primary issue is now CLOSED/not_planned (2026-07-13) after extensive attempt/PR history.
- **tailcallhq.github.io #217 / $100** — CLOSED/not_planned.
- **tailcallhq.github.io #216 / $100** — CLOSED/not_planned and assigned to `neo773`.

These rows are stale-board false positives and should not consume implementation seats.

### Missing canonical source — $100 advertised

Algora's **"Implement `to_schema` for a `QueryPlan`" / $100** row links to `tailcallhq/gargantua#16`. The live GitHub URL returns 404, and native GitHub connector lookups for both `tailcallhq/gargantua` and issue #16 return `NOT_FOUND`.

Disposition: **HOLD_CANONICAL_SOURCE_NOT_FOUND**. Never infer a live bounty from the marketplace description alone.

### Open but not fresh supply — $100 advertised

**rust-grpc #44 / $50** remains OPEN and unassigned, but it has 36 comments and sustained claim pressure through 2026. Visible recent claim/implementation carriers include PRs **#69, #73, #77**, plus additional later attempts. A fresh whole implementation would be collision churn.

**graphql-benchmarks #272 / $50** remains OPEN and unassigned, but the issue's last update is **2024-08-11**. Its 13-comment history already contains multiple attempts and claiming PRs **#279, #315, #352**. Without a fresh maintainer residual/acceptance signal, this is stale crowded work, not a 2026 revenue lane.

## Totals

- advertised current Algora open-board face value: **$1,000**
- canonically closed/pruned: **$800**
- canonical source missing: **$100**
- open but saturated/stale: **$100**
- **fresh whole-implementation supply: $0 / 0 rows**

## Fleet rule

Do not dispatch from a bounty marketplace's `OPEN` row alone. Bind the value row to the canonical source, then run canonical viability/collision/freshness checks. For Tailcall specifically, a future seat should only reopen one of the two currently OPEN rows if a fresh maintainer signal defines an uncovered residual and the claim/payment path is still live.

This artifact creates no claim, implementation, submission, award, or payment authority.
