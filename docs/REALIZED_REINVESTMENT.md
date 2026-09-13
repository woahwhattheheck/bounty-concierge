# Realized-outcome reinvestment review

`concierge.reinvestment_allocator` closes one commercial feedback loop: it turns **provider-reacquired realized RTC outcomes** into a deterministic, advisory owner review of which work families have enough evidence to deserve more next-cycle attention.

The production boundary deliberately does **not** accept a standalone realized-economics receipt, precomputed closeout result, captured wallet history, or history-source override. A self-hash proves integrity of a packet, not that the packet came from GitHub lifecycle state or canonical wallet settlement. The public compiler therefore establishes its cash authority in-process on every compile:

1. freeze the caller-owned manifest, payment bindings, effort log, taxonomy, and policy into one immutable JSON generation;
2. call `revenue_closeout.build_closeout_queue()` to reacquire current GitHub PR lifecycle/review state for the manifest scope;
3. call `revenue_settlement._query_canonical_history()` to reacquire canonical wallet history for the requested recipient wallet;
4. call `realized_unit_economics.compile_realized_unit_economics()` with those live observations, bindings, and complete effort evidence using `history_source="queried_wallet"`;
5. freeze that compiler-owned economics generation once, defensively verify its self-integrity and shape, and derive the family review only from the frozen generation.

A fully self-consistent forged receipt with invented cash/evidence hashes therefore has no public input slot. Provider read failure fails before a review is produced.

## Inputs

The public API is:

```python
compile_reinvestment_review(
    closeout_manifest_items,
    payment_bindings,
    effort_log,
    taxonomy,
    policy,
    capacity_minutes,
    *,
    wallet,
    max_closeout_pages=10,
)
```

The taxonomy uses schema `realized-reinvestment-taxonomy/v1` and maps every live realized `(repo, pr)` identity exactly once to a bounded lowercase family slug. The policy uses schema `realized-reinvestment-policy/v1` and supplies `minimum_samples`, `minimum_nonzero_cash_samples`, `minimum_median_rtc_per_hour`, and `maximum_family_capacity_bps`.

`taxonomy_sha256` and `policy_sha256` are **self-integrity digests only**. They do not authenticate an owner identity. Taxonomy and policy are caller-supplied planning inputs; the resulting artifact is for owner review, not proof that a particular person authored those inputs.

## Classification and allocation

For each family the receipt reports evidence-backed facts from the live in-process economics generation: sample count; fully paid / partially paid / zero-cash counts; verified RTC; active minutes; aggregate, median, and worst realized RTC/hour; and nonzero-cash share.

A family is `LEARN_MORE` until the explicit sample gates are met. Once the sample gates are met, zero total verified cash or median realized yield below the policy threshold produces `DEPRIORITIZE_REVIEW`. A family clearing every evidence threshold is `SCALE_REVIEW_ELIGIBLE`.

Eligible families receive an advisory review-capacity weight equal to aggregate realized RTC/hour multiplied by sample count. Integer review minutes are apportioned with exact rational arithmetic, a per-family basis-point cap, deterministic largest-remainder rounding, and stable family-slug tie breaks. If the caps cannot absorb all supplied capacity, the remainder stays explicitly unallocated.

The strongest state is `READY_FOR_OWNER_REINVESTMENT_REVIEW`. It is a planning review state, not an execution or earnings state.

## Verification semantics

`verify_reinvestment_receipt_current(...)` reacquires GitHub closeout state and canonical wallet history again and recompiles the entire receipt. Provider drift therefore makes an older receipt fail current verification.

`verify_receipt_integrity_only(...)` checks only the reinvestment receipt's self-hash. It is intentionally named so callers cannot confuse packet integrity with current cash provenance.

The CLI exposes only live compile and live re-verification:

```bash
python -m concierge.reinvestment_allocator compile \
  manifest.json bindings.json effort.json taxonomy.json policy.json \
  --wallet <recipient-wallet> --capacity-minutes 480 \
  --output reinvestment.json

python -m concierge.reinvestment_allocator verify-current \
  reinvestment.json manifest.json bindings.json effort.json taxonomy.json policy.json \
  --wallet <recipient-wallet> --capacity-minutes 480
```

There is no `--history` flag and no realized-receipt input.

## Ingress and mutation safety

File inputs are opened once, with no-follow/nonblocking flags where the platform exposes them, verified by `fstat()` to be regular files, and bounded by a max+1 read on that same descriptor. Duplicate JSON keys and non-finite constants fail closed. Output creation is exclusive and refuses an existing final path or final symlink.

Caller-owned semantic inputs are canonically snapshotted before the first provider call. The compiler-owned economics object is also canonically snapshotted once before downstream verification/projection, and output source references come from that frozen copy. Later concurrent mutation cannot pair evaluated economics with a different receipt/scope reference.

## Authority ceiling

This module never authorizes or performs a bounty claim, submission, maintainer/customer/sponsor contact, purchase, spend, wallet/provider/payment mutation, credential use, accounting or tax conclusion, future-revenue assertion, or guaranteed return. Advertised reward is never substituted for realized cash.

The receipt explicitly records that live closeout and wallet authority were reacquired, standalone economics/captured-history inputs were not accepted, taxonomy/policy digests are integrity-only, and all execution authorities are false.
