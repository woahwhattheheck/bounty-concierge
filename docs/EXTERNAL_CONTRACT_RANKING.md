# External contract partitioned ranking

`concierge.external_contract_ranker` turns **currently verified external paid-contract qualification receipts** into a decision-support queue for scarce implementation time.

It is deliberately separate from the canonical GitHub bounty rankers. External marketplace contracts carry different evidence, bid, account, screening, and submission-route semantics, and their native currencies must not be silently converted into one invented global score.

## Authority chain

Every candidate supplies both:

- the exact normalized snapshot consumed by `concierge.contract_qualification`; and
- the exact qualification receipt emitted for that snapshot.

Before an effort or award-probability estimate can influence ranking, the ranker calls `verify_contract_qualification_receipt(..., as_of=<trusted current UTC>)`. A tampered receipt, stale listing/evidence, elapsed deadline, newly-unsatisfied platform gate, non-ACTIONABLE disposition, or otherwise unverifiable qualification is excluded.

The production CLI **and public library API** own current UTC. Neither accepts a caller-selected `as_of`; deterministic timestamp injection exists only in a private evaluator used for tests/internal mechanics. This prevents historical replay from resurrecting an expired qualification as current prioritization authority. The CLI intentionally exposes no `--as-of` flag.

## Ranking contract

Qualified rows are partitioned by the qualification receipt's native currency only after the ranker rechecks that identity as exactly three ASCII uppercase letters (`[A-Z]{3}`). Ranking happens **only inside one currency partition**. This ranker-side check remains mandatory even after upstream qualification verification so Unicode lookalikes such as a Cyrillic-letter `UЅD` cannot form a visually deceptive partition distinct from `USD`.

Within a partition the order is:

1. estimated expected value per hour;
2. estimated expected value;
3. estimated award probability;
4. proposed bid amount;
5. estimated effort hours;
6. canonical source URL.

The first five comparisons use exact `Fraction` arithmetic derived from bounded exact decimals. Rendered decimal metrics are display only and never ordering authority.

There is no cross-currency comparison, exchange-rate estimate, or global winner. `global_winner` is always `null`; the receipt explicitly fixes `fx_conversion=false` and `cross_currency_ranking=false`.

## Request shape

The CLI accepts one strict JSON object with exactly one `candidates` key. Each candidate has exactly four fields:

```json
{
  "candidates": [
    {
      "snapshot": {"...": "exact external-contract qualification snapshot"},
      "qualification_receipt": {"...": "exact qualification receipt"},
      "estimated_effort_hours": "12.5",
      "estimated_award_probability": "0.35"
    }
  ]
}
```

Operator estimates must be exact decimal strings or integers. Binary floats and boolean aliases fail closed. Effort must be positive; award probability must be between 0 and 1 inclusive.

Duplicate JSON keys are rejected at every nesting level. `NaN`/`Infinity` are rejected. JSON integer tokens are bounded before integer conversion, and direct-library integer estimates/bid amounts are bounded before string formatting, so runtime-specific large-integer conversion limits cannot escape the module's `ExternalContractRankInputError` boundary. File input is size-bounded and must resolve to a regular file; final-component symlinks are refused on platforms with `O_NOFOLLOW`.

Example:

```bash
python -m concierge.external_contract_ranker request.json --json
```

## Duplicate-source fence

The same verified `canonical_source_url` may appear at most once in one ranking request. Duplicate authority is rejected request-wide before estimate validation and across currency partitions. A malformed estimate therefore cannot make its otherwise-valid twin survive, and two conflicting currency receipts cannot create two prioritization entries for the same contract.

## Output and custody

Each ranked row preserves the upstream `source_digest` and `qualification_digest`, the native currency, proposed bid amount, delivery days, operator estimates, and exact-derived decision metrics. `ranking_digest` binds the complete ranking projection, exclusions, evaluation time, and request cardinality for deterministic self-integrity.

That digest is not a signature, provider authentication, trusted timestamp, award proof, payment proof, or accounting record.

## Authority ceiling

This module is decision support only. It does **not**:

- submit a bid or marketplace form;
- accept terms or a contract;
- create or configure an account;
- complete KYC;
- spend money or fund a platform balance;
- establish that work was awarded;
- establish that payment was received;
- recognize revenue; or
- perform or infer FX conversion.

A high rank means only that, among currently-qualified opportunities in the **same native currency**, the operator-supplied estimates imply a higher priority under the documented exact ordering.
