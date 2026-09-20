# Bounty value router

`concierge.bounty_value_router` is the swarm's intake-value classifier. It sits
**before** deeper profitability, assignment, implementation, submission, and
settlement gates.

The checked-in operator policy is `policies/bounty_value_routing_v1.json`:

- **USD / USDC >= 50**: value class `VALUE_50_PLUS`; recommended route
  `bug-bounty`.
- **USD / USDC >= 10 and < 50**: `PILE_10_49`; recommended route
  `bounty-pile-10-49`.
- **Below 10**: `DROP_UNDER_10`.
- **No fresh, trusted, issue- or milestone-specific amount**:
  `HOLD_ISSUE_AMOUNT_UNVERIFIED`.

This router intentionally does **not** perform FX, estimate win probability,
decide claimability, or turn a possible/discretionary reward into a verified
amount.

## Evidence precedence

A value may route only from fresh amount evidence that v1 can bind
structurally to the candidate's **exact canonical GitHub issue**:

1. FIRST_PARTY evidence must use that exact issue URL; or
2. MAINTAINER evidence must use a canonical issuecomment URL on that exact
   issue.

Caller-supplied authority labels are not enough. External provider URLs,
aggregator rows, program-generic rows, milestone-wide rows, and evidence for a
different issue are retained as context but cannot promote this issue into the
active queue in v1. This prevents a mirror/provider URL plus a self-selected
FIRST_PARTY / PROVIDER / MAINTAINER label from nominating the active amount for
an unrelated canonical target.

This is a **source-binding** rule, not cryptographic proof that the quoted
amount appears in the source or that a comment author is really a maintainer.
Live contract/provenance collectors remain responsible for those stronger
claims before implementation or submission authority can exist.

Conflicting fresh source-bound issue amounts hold fail-closed rather than
choosing the largest number.

## No automatic micro-batch promotion

The `$10-49` route is a parking pile, not an alternate way to synthesize a
larger active bounty. Three $20 items remain three `PILE_10_49` items even
though their arithmetic sum is $60. Promotion of a bundled pile requires a new
owner decision and a separately verified bundle contract.

This is deliberately different from `fleet_economic_admission`, which can
evaluate explicitly compatible batches for profitability after intake.

## Example request

```json
{
  "schema": "bounty-value-routing/v1",
  "policy": {
    "schema": "bounty-value-routing-policy/v1",
    "max_evidence_age_seconds": 86400,
    "routes": {
      "main_queue": "bug-bounty",
      "pile_10_49": "bounty-pile-10-49"
    },
    "assets": {
      "USD": {"active_floor": "50", "pile_floor": "10"},
      "USDC": {"active_floor": "50", "pile_floor": "10"}
    }
  },
  "evaluated_at": "2026-09-19T23:00:00Z",
  "candidates": [
    {
      "work_id": "example-50",
      "canonical_source_url": "https://github.com/acme/widget/issues/50",
      "reward_evidence": [
        {
          "scope": "ISSUE_SPECIFIC",
          "authority": "FIRST_PARTY",
          "amount": "50",
          "asset": "USD",
          "evidence_url": "https://github.com/acme/widget/issues/50",
          "observed_at": "2026-09-19T22:30:00Z"
        }
      ]
    }
  ]
}
```

Run:

```bash
python -m concierge.bounty_value_router request.json --json
```

## Authority ceiling

Every receipt fixes the following false:

- claim authority
- implementation authority
- submission authority
- payment/wallet authority
- FX conversion
- automatic batch promotion

A `VALUE_50_PLUS` result is only a value-routing result. Existing availability,
collision, provider-assignment, engineering-effort, and payout gates still
apply.
