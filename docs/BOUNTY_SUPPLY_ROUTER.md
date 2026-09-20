# Bounty Supply Router

The supply router is an **offline queueing layer** for already captured bounty
evidence. It exists to keep a fast swarm from repeatedly querying providers
while preventing old snapshots from remaining actionable forever.

It does **not** fetch GitHub, Slack, marketplaces, exchange rates, or wallets.
It does **not** claim a bounty, contact a sponsor, merge external code, or treat
an advertised offer as earned payment. The existing
`concierge.bounty_qualification` gate remains authoritative for canonical
state, reward consistency, contribution-policy blocks, competition saturation,
and unsafe private-context requirements.

## Default routing policy

| Route | Fresh fixed USD evidence | Operational meaning |
| --- | ---: | --- |
| `ACTIVE` | `>= $50` | Eligible for the main work queue after normal collision checks |
| `MAYBE` | `$10 <= reward < $50` | Save only in `#bounty-pile-10-49`; do not consume active build capacity |
| `PRUNE` | `< $10` | Disregard for paid-work dispatch |
| `HOLD` | stale/incomplete/ambiguous or no trustworthy fixed USD floor | Refresh source/terms; do not infer value |

The **$50 ACTIVE / $10 MAYBE floors are fixed owner policy**, not caller
configuration. API calls that attempt to override either floor fail closed, and
the CLI does not expose floor override flags.

A qualification-level `REJECT` is routed to `PRUNE`/suppression. A
qualification-level `HOLD` stays `HOLD`. Fresh otherwise-actionable rows also
pass through `bounty_acceptance_safety_gate`; API-key, session-cookie,
`.env`, private-runtime, hidden-instruction, or private-reasoning disclosure
demands route `HOLD / UNTRUSTED_ACCEPTANCE_TEXT` without persisting source
prose.

RTC and other native-token amounts are intentionally **not converted to USD**.
An RTC-only listing therefore cannot satisfy the USD work floor through this
router. If the operator later captures a canonical fixed-USD contract, route
that new evidence instead of injecting an exchange-rate assumption.

## Freshness contract

Every candidate should carry an offset-aware `observed_at` timestamp identifying
when its canonical issue/reward/competition evidence was captured. Every route
run requires an explicit `evaluated_at` timestamp. Both are normalized to UTC
and bound into the receipt.

The default freshness ceiling is **900 seconds (15 minutes)**:

- age `<= 900` seconds is fresh;
- age `> 900` seconds routes `HOLD / SOURCE_EVIDENCE_EXPIRED`;
- missing observation time routes `HOLD / SOURCE_OBSERVATION_MISSING`;
- a future-dated observation routes `HOLD / SOURCE_OBSERVATION_IN_FUTURE`.

The ceiling is configurable with `--max-age-seconds`, and its exact value is
also receipt-bound. Re-running an unchanged snapshot later therefore changes its
age/receipt and eventually removes it from ACTIVE/MAYBE until source evidence is
refreshed.

## Input contract

Each candidate is a normalized snapshot accepted by
`bounty_qualification.qualify_dispatch`, plus:

- `repo`: canonical `owner/repository` slug;
- `number`: positive issue number;
- `observed_at`: offset-aware ISO-8601 canonical observation time.

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
python -m concierge.bounty_supply supply.json \
  --evaluated-at 2026-09-20T01:00:00Z --json

python -m concierge.bounty_supply supply.json \
  --evaluated-at 2026-09-20T01:00:00Z --max-age-seconds 900
```

## Deduplication and receipts

GitHub repository names are case-folded for identity, producing a key like
`owner/repo#123`.

Multiple observations for the same issue are compared by their **safe normalized
source semantics**, not by raw prose. If those semantics are identical, the
newest observation is authoritative for freshness and `source_row_count`
records how many generations were collapsed. This prevents an older duplicate
from making a refreshed row stale. Acceptance-safety classification is bound to
the router's trusted `evaluated_at`, so missing/stale/fresh/future clocks of
identical source text cannot mint a false `CONFLICTING_DUPLICATE_EVIDENCE`.

A future-dated newest observation still fails closed rather than falling back to
an older row.

If duplicate generations disagree on safe qualification/economic evidence, the
router **fails closed** with `CONFLICTING_DUPLICATE_EVIDENCE`. A canonical
qualification `REJECT` is never downgraded by a conflicting permissive row:
REJECT conflicts route `PRUNE`; HOLD conflicts stay `HOLD`; otherwise
conflicting ACTIONABLE observations route `HOLD`. Conflict receipts bind the
sorted safe candidate-semantic signatures, so distinct conflict sets cannot
alias to the same receipt. The router never chooses the higher reward or the
more permissive semantic state.

Rows and the aggregate result carry deterministic SHA-256 receipts over
canonical JSON. Input order therefore does not change the output. Receipts bind
safe normalized evidence, observation/evaluation time, freshness age, and policy;
raw issue bodies and comments are never copied into persisted route rows.

## Provider-pressure workflow

1. Capture canonical issue/PR/reward evidence once and stamp `observed_at`.
2. Store or hand off the normalized snapshot.
3. Route offline with an explicit current `evaluated_at`.
4. Work only fresh `ACTIVE` rows after a collision check.
5. Send fresh `MAYBE` rows to the saving pile, not the build queue.
6. When evidence ages out, refresh canonical source instead of replaying old state.
7. Re-capture canonical evidence again before a real claim/submission if the
   source may have changed.

This split keeps source retrieval bounded while queue economics stay cheap,
deterministic, freshness-aware, and reviewable.
