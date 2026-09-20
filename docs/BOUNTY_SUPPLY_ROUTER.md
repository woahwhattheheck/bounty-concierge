# Bounty Supply Router

The supply router is an **offline queueing layer** for already captured bounty
evidence. It exists to keep a fast swarm from repeatedly querying providers and
from spending build capacity on low-value, stale, ambiguous, or duplicate work.

It does **not** fetch GitHub, Slack, marketplaces, exchange rates, or wallets.
It does **not** claim a bounty, contact a sponsor, merge external code, or treat
an advertised offer as earned payment. The existing
`concierge.bounty_qualification` gate remains authoritative for canonical
state, reward consistency, contribution-policy blocks, competition saturation,
and unsafe private-context requirements.

## Default routing policy

| Route | Fixed USD evidence | Operational meaning |
| --- | ---: | --- |
| `ACTIVE` | `>= $50` | Eligible for the main work queue after normal collision checks |
| `MAYBE` | `$10 <= reward < $50` | Save only in `#bounty-pile-10-49`; do not consume active build capacity |
| `PRUNE` | `< $10` | Disregard for paid-work dispatch |
| `HOLD` | no trustworthy fixed USD floor | Re-verify source/terms; do not infer a value |

A qualification-level `REJECT` is routed to `PRUNE`/suppression. A
qualification-level `HOLD` stays `HOLD`.

RTC and other native-token amounts are intentionally **not converted to USD**.
An RTC-only listing therefore cannot satisfy the USD work floor through this
router. If the operator later captures a canonical fixed-USD contract, route
that new evidence instead of injecting an exchange-rate assumption.

## Input contract

Each candidate is a normalized snapshot accepted by
`bounty_qualification.qualify_dispatch`, plus:

- `repo`: canonical `owner/repository` slug.
- `number`: positive issue number.

Canonical audit fields should include:

```json
{
  "issue_state": "open",
  "open_pr_count": 0,
  "stale_listing_signal": false,
  "search_truncated": false
}
```

The CLI accepts one snapshot, a list of snapshots, or
`{"candidates": [...]}`.

```bash
python -m concierge.bounty_supply supply.json --json
python -m concierge.bounty_supply supply.json \
  --active-floor-usd 50 --maybe-floor-usd 10
```

## Deduplication and receipts

GitHub repository names are case-folded for identity, producing a key like
`owner/repo#123`. Identical safe evidence collapses into one row and increments
`source_row_count`.

If two rows for the same canonical issue produce different safe evidence, the
router **fails closed** to `HOLD` with
`CONFLICTING_DUPLICATE_EVIDENCE`. It never chooses the higher reward or the
more permissive row.

Rows and the aggregate result carry deterministic SHA-256 receipts over
canonical JSON. Input order therefore does not change the output. Receipts bind
safe normalized evidence and policy; raw issue bodies and comments are never
copied into persisted route rows.

## Provider-pressure workflow

1. Capture canonical issue/PR/reward evidence once.
2. Store or hand off the normalized snapshot.
3. Run the router locally/offline as many times as needed.
4. Work only `ACTIVE` rows after a fresh collision check.
5. Send `MAYBE` rows to the saving pile, not the build queue.
6. Re-capture canonical evidence before a real claim/submission if the source may
   have changed.

This split keeps source retrieval expensive and infrequent while queue economics
remain cheap, deterministic, and reviewable.
