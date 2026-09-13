# Bounty Cash-Cycle Review Desk

`concierge.cash_cycle_review` turns the **already evidence-bound** post-merge payout state into repository-level timing observations that an owner can review when deciding where future paid work deserves attention.

It does **not** decide that a repository owner is the payer or sponsor. Repository identity is only a stable grouping key. It does not contact a sponsor/maintainer, submit a claim, mutate ranking, touch a wallet/provider, initiate a payout, assert a debt/receivable, or recognize cash/revenue.

## Why this exists

The existing revenue rail answers per-work-item questions:

1. `revenue_closeout` distinguishes merge/acceptance state from payment.
2. `payout_escalation` binds exact wallet-history rows to exact merged work and classifies transfer-timing follow-up.
3. `revenue_settlement` can reconcile confirmed incoming transfers to advertised RTC amounts.

The missing portfolio view was: **how long after merge did confirmed incoming-transfer evidence appear for work in each canonical repository, and has the observed history crossed an explicit owner review threshold?**

The desk fills that gap without converting timing history into a payer-reliability label or an autonomous rank adjustment.

## Authority boundary

`cash_cycle_review` trusts no candidate-provided terminal label. Every compile first runs `payout_escalation.compile_payout_escalation(...)` over the exact closeout snapshot, exact wallet-history capture, exact operator bindings, and verifier-owned current UTC.

Only upstream rows whose reverified action is `run_revenue_settlement` become confirmed-lag observations. Pending, confirming, failed, missing, or otherwise nonterminal transfer evidence never becomes a confirmed sample. The module then defensively rechecks the selected raw rows before deriving timestamps.

The reported timestamp is the canonical wallet row's transaction/initiation timestamp. **It is not inferred confirmation time.** For that reason the metric names say `confirmed_transfer_*_at` / `*_lag_seconds`: the transfer is confirmed evidence at evaluation time, while its timestamp remains the provider row's transaction/initiation timestamp.

## Inputs

Compilation takes four JSON inputs already used by the landed payout rail plus one local review policy:

- `closeout.json` — schema-v1 merged RTC closeout snapshot accepted by `payout_escalation`.
- `history.json` — exact canonical or normalized RustChain wallet-history capture with wallet provenance.
- `bindings.json` — exact payout-evidence bindings accepted by `payout_escalation`.
- `policy.json` — owner review thresholds only.

Policy has an exact shape:

```json
{
  "schema_version": 1,
  "minimum_confirmed_samples": 3,
  "review_lag_hours": 48
}
```

Both threshold values are bounded positive integers. Booleans are rejected even though Python otherwise treats `bool` as a subclass of `int`.

## Output semantics

Each confirmed observation records:

- case-folded repository identity and PR number;
- exact `merged_at`;
- first and last confirmed-transfer row timestamps;
- first and last post-merge lag in integer seconds;
- number and SHA-256 identities of exact bound wallet-history rows;
- `cash_claim: none_from_cash_cycle_review`.

Every repository present in the upstream payout receipt remains visible, even if it has zero confirmed samples. Repository summaries contain:

- `observed_merged_item_count`;
- `confirmed_sample_count`;
- min first-transfer lag;
- min / exact median / max last-transfer lag;
- source PR identities;
- the explicit policy thresholds;
- one of three states.

### States

`INSUFFICIENT_CONFIRMED_HISTORY`
: Fewer than `minimum_confirmed_samples` confirmed observations. Pending/failed/missing transfer histories land here rather than disappearing.

`OBSERVED_NO_REVIEW_TRIGGER`
: Enough confirmed samples exist and the exact median last-transfer lag is below the owner threshold.

`READY_FOR_OWNER_CASH_CYCLE_REVIEW`
: Enough confirmed samples exist and the exact median last-transfer lag is **at or above** `review_lag_hours`. This authorizes only an owner review; it does not label a payer, contact anyone, or alter ranking.

Even-sized medians remain exact: a half-second median is serialized as decimal text such as `"10.5"` and threshold comparison is done with doubled integers, avoiding binary floating-point decisions.

## Evidence custody and determinism

The receipt binds SHA-256 digests of the exact closeout object, history capture, bindings object, the exact upstream payout-escalation receipt, and normalized policy. It also binds `evaluated_at` and a canonical receipt digest.

Analytics are deterministic and sorted by case-folded repository + PR, so equivalent item ordering does not change observation or repository rows. The source digests intentionally preserve exact list ordering. A reordered source therefore has the same analytics but a different custody digest; this is a feature, not a ranking difference.

`verify_cash_cycle_review(...)` recomputes the historical receipt from the supplied exact inputs at the receipt's own `evaluated_at`. It rejects malformed receipts, source/policy drift, tampering, upstream revalidation failure, and verifier-time rollback. Historical verification does not claim the old snapshot is still current; run a fresh compile for current state.

## CLI

Production compilation owns current UTC. There is intentionally no `--as-of` override:

```bash
python -m concierge.cash_cycle_review compile \
  closeout.json history.json bindings.json policy.json cash-cycle.json
```

The output path is create-exclusive. Existing files and symlinks are refused; inputs are bounded regular files, UTF-8, duplicate-key-strict JSON, with `O_NOFOLLOW` used where the platform supports it.

Verify an existing receipt against its exact inputs:

```bash
python -m concierge.cash_cycle_review verify \
  cash-cycle.json closeout.json history.json bindings.json policy.json
```

Verification prints `{"valid":true}` and exits 0 on success; invalid evidence/receipt returns a nonzero exit.

## What this is deliberately not

This desk does not provide:

- payer or sponsor identity inference;
- a "good/bad sponsor" score;
- probability of winning future work;
- expected revenue or accounting treatment;
- FX conversion, interest, debt, or tax/legal conclusions;
- automatic opportunity ranking changes;
- settlement demands or outbound messaging;
- wallet/provider writes or transfer initiation.

Those ceilings are also serialized in every receipt so downstream consumers cannot silently upgrade timing observations into stronger authority.

## Validation

Focused tests cover confirmed/pending/failed/no-transfer paths, partial/multiple confirmed evidence, exact threshold boundaries, insufficient-history visibility, odd/even medians, repository partitioning/case-folding, bool/int policy traps, duplicate/NaN JSON, source/tamper/time-rollback verification failures, duplicate history evidence, transfer-before-merge/future evidence, exact source-order custody, output overwrite/symlink refusal, upstream error propagation, and a real `payout_escalation` integration fixture.

Run both normal and optimized modes:

```bash
python -m unittest -v tests.test_cash_cycle_review
python -O -m unittest -v tests.test_cash_cycle_review
```
