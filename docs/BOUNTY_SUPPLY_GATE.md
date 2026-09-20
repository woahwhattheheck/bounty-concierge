# Bounty supply snapshot normalizer

`concierge.bounty_supply_gate` is an upstream **snapshot normalizer**, not an availability attestor and not a final bounty-dispatch gate. It binds a semantically verified bounty-value receipt to a same-identity caller-supplied orchestration snapshot, applies conservative stop/hold normalization, and forwards only normalized rows to `bounty_canonical_viability`. It does **not** publish to the active `bug-bounty` queue.

This layer exists to make two boundaries explicit. First, the economic input must be the actual verified `bounty_value_router` receipt for the exact `(work_id, canonical_source_url)` candidate; a digest-shaped wrapper is insufficient. Second, lifecycle/carrier fields supplied by the orchestrator are **not authenticated source evidence**. They can stop or hold a row conservatively, but a positive result can never establish that the primary source is actually open, unheld, unassigned, or collision-free. The downstream canonical-viability layer must independently re-fetch/reverify those facts before any claim or implementation decision.

## Trust model

The receipt records its trust boundary directly:

- `value_receipt = SEMANTICALLY_VERIFIED_EXACT_JSON` — the full upstream receipt is recursively restricted to exact built-in JSON types before semantic verification, preventing caller-owned container/scalar subclasses from participating in reflected equality or replay.
- `availability = CALLER_ASSERTED_UNVERIFIED_ORCHESTRATOR_SNAPSHOT` — primary state, maintainer hold, assignment, census, carrier, and marketplace fields are normalization inputs only.
- `downstream_canonical_reverification_required = true` — no source/collision assertion from this layer may be treated as proof by `bounty_canonical_viability` or later mutation logic.

The authority object separately fixes `source_or_collision_evidence_authority = false` together with no claim, implementation, submission, queue-publication, payment, wallet, or marketplace-override authority.

## Inputs and identity binding

At one exact evaluation timestamp, the normalizer consumes:

1. a semantically valid `bounty-value-routing-receipt/v1`; and
2. exactly one caller-supplied availability snapshot for every candidate in that receipt.

The candidate sets must match exactly on `(work_id, canonical_source_url)`. Missing, extra, or duplicate pairs fail closed. Empty waves fail closed. The normalizer independently checks the owner floor: the selected nominal amount must be fixed **USD or USDC >= 50**. It does not trust the upstream `VALUE_50_PLUS` label alone, so a weakened caller policy cannot promote a $5 row.

## Snapshot normalization

The snapshot carries primary state, optional state reason, maintainer stop/hold, exclusive-assignment flag, assignees, source-census completeness, carrier-census completeness, known active/merged carrier count and URLs, and marketplace advertised-open state.

Freshness uses the full timestamp precision: the maximum is **exactly 900 seconds**. A 900.999-second snapshot is stale even though its floored whole-second age would display as 900. The receipt records exact age in microseconds. Future timestamps fail input validation.

A positive normalization result is `SNAPSHOT_NORMALIZED_FOR_VIABILITY`, with next route `bounty-canonical-viability`. It means only that the verified value receipt and the caller snapshot are structurally coherent enough for the downstream verifier to perform its own canonical reads.

Conservative negative dispositions are:

- `SUPPRESS_PRIMARY_CLOSED`
- `SUPPRESS_MAINTAINER_HOLD`
- `SUPPRESS_EXCLUSIVE_ASSIGNMENT`
- `SUPPRESS_EXISTING_CARRIER`
- `HOLD_AVAILABILITY_STALE`
- `HOLD_PRIMARY_STATE_UNKNOWN`
- `HOLD_SOURCE_CENSUS_INCOMPLETE`
- `HOLD_CARRIER_CENSUS_INCOMPLETE`
- `HOLD_VALUE_NOT_ACTIVE`
- `HOLD_MARKETPLACE_STATE_NOT_OPEN`

A known carrier suppresses even when the full carrier census is incomplete. A primary-closed snapshot suppresses even when the marketplace bit says open and records `MARKETPLACE_OPEN_CONFLICTS_WITH_PRIMARY`. A marketplace state of closed or unknown now conservatively holds for downstream recheck; marketplace `OPEN` is still **not** proof of primary availability.

## Strict transport and replay

The CLI rejects duplicate JSON keys and non-finite constants (`NaN`, `Infinity`, `-Infinity`). Compilation recursively rejects caller-owned JSON subclasses before invoking the upstream value verifier. Receipt verification repeats the same exact-type ownership check before semantic replay. Tampered child receipts, output tampering, identity/cardinality mismatch, stale/future observations, and extra/missing snapshots all fail closed.

Run a request with:

```bash
python -m concierge.bounty_supply_gate request.json --json
```

Exit code `0` means every row is `SNAPSHOT_NORMALIZED_FOR_VIABILITY`. Exit code `2` means at least one row is suppressed or held. Input/transport errors use argparse's non-zero error exit.
