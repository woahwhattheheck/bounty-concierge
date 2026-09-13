# Cash-calibrated opportunity portfolios

`concierge.cash_calibrated_portfolio` closes one deliberately narrow feedback loop
between the evidence-bound realized unit-economics receipt and the existing
estimated-EV portfolio allocator.

The rule is intentionally asymmetric:

- current reward stays the canonical advertised **USD** reward from the intake gate;
- effort and initial win probability stay operator estimates;
- settled history may only **lower** that win probability;
- realized **RTC amounts are never converted, normalized, or multiplied into USD**;
- current eligibility, deadline, collision, capacity, and exact-subset selection stay
  delegated to the existing canonical ranker and exact portfolio allocator.

This is decision support. It does not claim or submit work, contact maintainers,
move money, infer payouts from merge state, perform FX conversion, or create
accounting/tax authority.

## Historical outcome model

Repository identity comes only from the current candidate's canonical GitHub issue
URL (`https://github.com/<owner>/<repo>/issues/<n>`). Candidate-supplied repository
labels are ignored.

For the same repository, history from a schema-v1
`realized_unit_economics` receipt is classified as:

| historical state | cash status | calibration outcome |
| --- | --- | --- |
| `MERGED` | `verified_paid` | positive terminal |
| `MERGED` | `partially_verified` | positive terminal |
| `CLOSED_UNMERGED` | `not_inferred` | negative terminal |
| `MERGED` | `not_inferred` | unresolved |
| `OPEN` | `not_inferred` | unresolved |
| `HEAD_MOVED` | `not_inferred` | unresolved |

Unresolved rows never enter the denominator.

After `minimum_terminal_samples` (default `2`) same-repository terminal outcomes,
the module computes

```text
cash_observation_rate =
    positive_terminal / (positive_terminal + negative_terminal)
```

and floors the rate to six decimal places. The calibrated probability is

```text
min(operator_estimated_win_probability, cash_observation_rate)
```

so history cannot improve an estimate. With insufficient or unmapped history, the
operator estimate is left unchanged.

The downstream allocator then re-runs canonical qualification and solves the same
bounded exact maximum-estimated-EV problem it already solved before this layer.

## Receipt boundary

The economics packet is not accepted merely because its SHA-256 self-hash matches.
This layer also revalidates its schema-v1 envelope, canonical scope hash, summary
totals, per-item state/cash consistency, evidence-hash uniqueness, deterministic
ranking, and canonical ordering.

That still does **not** turn a self-hash into provider authentication. The
`realized_unit_economics` module itself documents `verify_receipt()` as
self-integrity only. Production use should feed the output produced from its
evidence-bound settlement path; this calibration layer does not re-query the
wallet or upgrade receipt self-integrity into cash authority.

The output repeats that ceiling in `authority` and adds its own deterministic
`calibration_receipt_sha256`.

## CLI

Prepare the same request shape used by `concierge.portfolio_allocator`:

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

Then run:

```bash
python -m concierge.cash_calibrated_portfolio \
  request.json \
  realized-unit-economics.json
```

Useful flags:

```text
--minimum-terminal-samples N
--saturation-threshold N
--summary
```

An empty selected portfolio returns exit status `2`, matching the existing
portfolio allocator convention. Malformed/tampered evidence and malformed inputs
fail closed.
