# Active claim portfolio v2

`concierge.active_claim_portfolio` is a fail-closed internal custody gate for paid-work opportunities. It prevents two workers from silently taking the same canonical GitHub issue, enforces worker/sponsor/total capacity, binds active work to an exact authority generation, and exposes stale custody for review.

## Security boundary

Only `compile_live_active_claim_portfolio()` may return `READY_FOR_INTERNAL_CLAIM`.

The live path accepts identity, worker/sponsor assignment, reward metadata, and an optional discovery URL. It does **not** accept qualification receipts, availability receipts, `observed_at`, or an `as_of` clock. It obtains current UTC itself, then independently calls:

- `revenue_intake.qualify_live_revenue_intake()` for canonical reward/provenance/competition qualification; and
- `bounty_availability.inspect_bounty_availability()` for a separate stable-generation availability read.

Either read failing or returning non-object data fails closed. Provider exception text is not copied into the receipt. GitHub `owner/repo` identity is case-folded before live reads, candidate grouping, generation binding, event lineage, and capacity accounting.

The implementation keeps the previously reviewed v1 deterministic ledger engine in the private `_active_claim_portfolio_core` module. The public v2 adapter supplies only verifier-owned live evidence to that engine. The fixed neutral `observed_at` used at the private-core boundary is deliberate: current authority bytes, not wall time, define an opportunity generation; event age is measured from verifier-owned current UTC.

## Historical replay

`compile_replay_active_claim_portfolio(..., as_of=...)` and the legacy `compile_active_claim_portfolio` alias are audit-only. A clean unclaimed historical row becomes `REPLAY_ONLY_HOLD` with `HISTORICAL_REPLAY_NON_DISPATCH`; replay can never authorize new work. `verify_active_claim_portfolio_receipt()` deterministically regenerates replay receipts and rejects receipt or policy drift.

## CLI

Live current decision (no `--as-of` option exists):

```bash
python -m concierge.active_claim_portfolio live \
  --candidates candidates.json --events events.json --policy policy.json \
  --output receipt.json
```

Historical audit:

```bash
python -m concierge.active_claim_portfolio replay \
  --candidates candidates.json --events events.json --policy policy.json \
  --as-of 2026-09-14T23:45:00Z --output receipt.json
```

`compile` remains an alias for replay so old automation fails safe. `verify-replay` checks a replay receipt against exact inputs.

## Authority limits

A READY result is internal work-custody permission only. It is not a GitHub claim, sponsor contact, upstream submission, acceptance, payout, cash, or revenue assertion. The receipt records these limits explicitly.

Reward values remain per-opportunity metadata; currencies are never summed or converted.
