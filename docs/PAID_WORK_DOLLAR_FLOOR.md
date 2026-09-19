# Paid-work dollar floor

`concierge.paid_work_dollar_floor` is the first economic routing gate for new paid-work intake.

It enforces the owner's current labor floor **before** the more detailed
`paid_work_effort_value_gate` spends time on engineering effort, congestion,
acceptance, payout-route, account/KYC, or model/tool-cost analysis.

## Owner routing contract

For fresh, verified **USD cash** payout evidence:

| Verified amount | Decision | Route |
|---|---|---|
| `$50+` | `ACTIVE_REVIEW` | `main-bounty-queue` |
| `$10–49.99…` | `PILE_SAVE_UP` | `bounty-pile-10-49` |
| `<$10` | `PRUNE_BELOW_FLOOR` | discard |

`ACTIVE_REVIEW` does **not** mean "claim this." It means only that the item is
large enough to deserve normal downstream economics and eligibility checks.
The existing paid-work gate still applies reward/hour, known cost, deadline,
claim congestion, first-party acceptance, payout-route and account/KYC gates.

The low-dollar pile is inventory, not an execution queue. A pile item may later
become economical as a deliberately promoted compatible bundle, but this
compiler grants no build/application/PR authority.

## Verification and value custody

The amount must be backed by fresh evidence whose authority is either:

- `FIRST_PARTY`, or
- `SETTLEMENT_PLATFORM`.

An unverified amount is `HOLD_VERIFY_AMOUNT`; it is never guessed from labels
such as "Maybe Rewarded."

This gate performs **no FX** and **no noncash valuation**. Non-USD or noncash
evidence is `HOLD_VALUE_NONCOMPARABLE`, even if the nominal number is large.

Evidence older than `max_evidence_age_seconds` is held for refresh. Future
evidence is malformed input.

## Policy

The checked-in policy is `policies/paid_work_dollar_floor_v1.json`:

```json
{
  "schema": "paid-work-dollar-floor-policy/v1",
  "currency": "USD",
  "pile_floor": "10",
  "active_floor": "50",
  "max_evidence_age_seconds": 86400,
  "pile_route": "bounty-pile-10-49",
  "active_route": "main-bounty-queue"
}
```

The downstream fleet/effort policies also use `$50` as the USD
`min_single_reward`, while retaining the independent `$100/agent-hour` floor.
That means a `$50+` item is allowed into consideration but can still be rejected
as too expensive for its expected effort.

## Request

```json
{
  "schema": "paid-work-dollar-floor/v1",
  "evaluated_at": "2026-09-19T23:00:00Z",
  "policy": {
    "schema": "paid-work-dollar-floor-policy/v1",
    "currency": "USD",
    "pile_floor": "10",
    "active_floor": "50",
    "max_evidence_age_seconds": 86400,
    "pile_route": "bounty-pile-10-49",
    "active_route": "main-bounty-queue"
  },
  "candidate": {
    "work_id": "org-repo-123",
    "canonical_source_url": "https://github.com/org/repo/issues/123",
    "payout_evidence": {
      "state": "VERIFIED",
      "amount": "75",
      "currency": "USD",
      "unit_type": "CASH",
      "authority": "FIRST_PARTY",
      "evidence_url": "https://sponsor.example/bounties/123",
      "observed_at": "2026-09-19T22:00:00Z"
    }
  }
}
```

For an amount not yet verified, use only:

```json
{
  "state": "UNVERIFIED",
  "evidence_url": "https://provider.example/task/123",
  "observed_at": "2026-09-19T22:00:00Z"
}
```

Do not include an amount, currency, unit type, or authority in an
`UNVERIFIED` record.

## CLI

```bash
python -m concierge.paid_work_dollar_floor request.json
python -m concierge.paid_work_dollar_floor request.json --json
```

The CLI returns `0` only for `ACTIVE_REVIEW`; pile/prune/hold outcomes return
`2`, making accidental active dispatch fail closed.

## Authority ceiling

Every receipt states that it is advisory only and grants no external claim,
submission, outbound-contact, payment, cash/revenue, FX, or noncash-valuation
authority.
