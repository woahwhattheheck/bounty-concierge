# Realized-outcome reinvestment review

`concierge.reinvestment_allocator` closes one narrow commercial loop: it turns an already evidence-bound `realized_unit_economics` receipt into a deterministic **owner review** over which work families have earned more attention.

It does not claim that a merged PR was paid, does not use advertised rewards as cash, and does not predict or guarantee future revenue. Settlement authority stays upstream. This layer only accepts the exact landed RTC realized-economics receipt, revalidates its canonical receipt digest, scope digest, summary, ranking, item rates, settlement-status consistency, and evidence identities, then groups those exact PR identities by an owner-authored taxonomy.

## Inputs

The source is the JSON emitted by `python -m concierge.realized_unit_economics`. The taxonomy has schema `realized-reinvestment-taxonomy/v1` and maps every source `(repo, pr)` exactly once to a bounded lowercase family slug. Its `taxonomy_sha256` is the canonical SHA-256 of the object without that digest field.

The policy has schema `realized-reinvestment-policy/v1` and contains `minimum_samples`, `minimum_nonzero_cash_samples`, `minimum_median_rtc_per_hour`, and `maximum_family_capacity_bps`. `policy_sha256` binds the exact policy object. The owner also supplies an integer `capacity_minutes` for the next review cycle; this is capacity planning metadata, not a reservation or spend authorization.

## Classification

For each family the compiler reports only evidence-backed facts: item count; fully/partially/zero-cash counts; verified RTC; active minutes; aggregate, median, and worst realized RTC/hour; and the nonzero-cash share.

A family is `LEARN_MORE` until the explicit sample gates are met. After the sample gates, zero total verified cash or median realized yield below the owner threshold produces `DEPRIORITIZE_REVIEW`. A family clearing those checks is `SCALE_REVIEW_ELIGIBLE`. No title, issue text, buyer message, or model judgment enters classification.

Eligible families receive an advisory review-capacity weight equal to aggregate realized RTC/hour multiplied by sample count. Capacity is apportioned with exact rational arithmetic, per-family basis-point caps, deterministic largest-remainder rounding, and family-slug tie breaks. If policy caps make it impossible to allocate all owner-supplied capacity, the remainder stays explicitly unallocated.

## Authority ceiling

The strongest state is `READY_FOR_OWNER_REINVESTMENT_REVIEW`. It authorizes no bounty claim, submission, external contact, provider or wallet mutation, purchase, spend, credential use, accounting/tax conclusion, future-revenue assertion, or guaranteed return. The output deliberately fixes all of those authorities false.

## CLI

```bash
python -m concierge.reinvestment_allocator compile \
  realized.json taxonomy.json policy.json \
  --capacity-minutes 480 --output reinvestment.json

python -m concierge.reinvestment_allocator verify \
  reinvestment.json realized.json taxonomy.json policy.json \
  --capacity-minutes 480
```

JSON parsing rejects duplicate keys. Output creation is exclusive and will not overwrite an existing path (including a final symlink). `verify` fully recompiles from the exact source, taxonomy, policy, and capacity; any receipt or input drift fails.
