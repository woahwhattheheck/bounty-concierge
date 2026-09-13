# Cash-calibrated opportunity portfolios

`concierge.cash_calibrated_portfolio` closes one deliberately narrow feedback loop
between settled-cash evidence and the existing exact estimated-EV portfolio
allocator **without trusting a standalone economics packet**.

The production authority path is intentionally strict:

1. the caller supplies an operator-owned paid-work manifest, payment bindings and
   effort log;
2. `revenue_closeout.build_closeout_queue()` re-reads current GitHub PR state;
3. `revenue_settlement._query_canonical_history()` re-reads the configured wallet
   provider;
4. `compile_realized_unit_economics()` reconciles those two live observations in
   the same process;
5. only the resulting dimensionless same-repository terminal outcome rate may
   lower an operator probability;
6. the existing canonical ranker and exact portfolio allocator retain all
   eligibility, reward, deadline, collision and capacity semantics.

A self-consistent `realized_unit_economics` JSON file is **not an input** to this
module. Captured/offline wallet history is also not accepted by the production
calibration path. This is deliberate: SHA-256 self-integrity cannot prove that
historical PR or wallet observations actually came from their providers.

## Calibration rule

Repository identity comes only from the current candidate's canonical GitHub
issue URL (`https://github.com/<owner>/<repo>/issues/<n>`). Candidate-supplied
repository labels are ignored.

After live provider reacquisition and settlement reconciliation, same-repository
history is classified as:

| live/reconciled state | cash status | calibration outcome |
| --- | --- | --- |
| `MERGED` | `verified_paid` | positive terminal |
| `MERGED` | `partially_verified` | positive terminal |
| `CLOSED_UNMERGED` | `not_inferred` | negative terminal |
| `MERGED` | `not_inferred` | unresolved |
| `OPEN` | `not_inferred` | unresolved |
| `HEAD_MOVED` | `not_inferred` | unresolved |

Unresolved rows never enter the denominator. After `minimum_terminal_samples`
(default `2`) same-repository terminal outcomes, the module computes

```text
cash_observation_rate =
    positive_terminal / (positive_terminal + negative_terminal)
```

and floors the rate to six decimal places. The calibrated probability is

```text
min(operator_estimated_win_probability, cash_observation_rate)
```

so history can never improve an operator estimate. With insufficient or unmapped
history, the operator estimate is unchanged.

Realized RTC **amounts** never enter current USD reward math. The only historical
signal passed into allocation is the dimensionless terminal outcome rate. There
is no RTC/USD conversion.

## Why live provider reacquisition matters

The repository's realized-economics receipt intentionally provides deterministic
self-integrity, not provider authentication. A caller can recompute a SHA-256 for
new JSON. Treating that self-hash as cash-history authority would let a fabricated
pair of `CLOSED_UNMERGED/not_inferred` rows dampen a 0.9 operator estimate to zero.

This module therefore does not expose an API parameter for:

- a precompiled economics receipt;
- precomputed closeout results;
- wallet-history rows;
- `history_source` / `history_wallet` overrides.

GitHub lifecycle and wallet history are acquired inside the production call. If
either provider read fails, calibration fails before the allocator runs.

The output's `authority` object records that boundary explicitly:

- `closeout_state = live_github_reacquired_in_process`;
- `wallet_history = canonical_provider_reacquired_in_process`;
- `standalone_economics_receipt_accepted = false`;
- `captured_wallet_history_accepted = false`;
- `cash_evidence_authority = reacquired_not_inherited`.

This still does not create payout, accounting or tax authority. It only uses the
repository's existing evidence-bound settlement semantics after reacquiring the
underlying provider observations.

## CLI

The request uses the same candidate shape as `concierge.portfolio_allocator`:

```json
{
  "candidates": [
    {
      "snapshot": {},
      "estimated_effort_hours": "4",
      "estimated_win_probability": "0.7",
      "hours_until_deadline": "12"
    }
  ],
  "skills": ["python"],
  "capacity_hours": "8"
}
```

The other three files are **inputs to the live rebuild**, not provider-result
snapshots:

- a schema-v1 `revenue_closeout` operator manifest;
- schema-v1 settlement payment bindings;
- schema-v1 `operator_active_minutes` effort log.

Run:

```bash
python -m concierge.cash_calibrated_portfolio \
  request.json \
  closeout-manifest.json \
  payment-bindings.json \
  effort.json \
  --wallet <recipient-wallet>
```

There is deliberately no `--history` and no economics-receipt argument.

Useful flags:

```text
--minimum-terminal-samples N
--saturation-threshold N
--max-closeout-pages N
--summary
```

Local JSON input uses bounded regular-file reads, rejects duplicate keys and
non-finite values, and fails closed on oversize/growing files. An empty selected
portfolio returns exit status `2`, matching the canonical portfolio allocator.

## Authority ceiling

Decision support only. This path does not claim or submit work, contact
maintainers, mutate providers, wallets or payments, infer debt or earned revenue,
perform FX conversion, or create accounting/tax authority. Provider reads are
read-only; the downstream result remains an estimated portfolio for owner use.
