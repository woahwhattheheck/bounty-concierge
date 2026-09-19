# Paid-work effort/value gate

`concierge.paid_work_effort_value_gate` is the fleet's fail-closed admission boundary for spending engineering capacity on explicit paid work.

It exists because **an advertised reward is not enough**. A paid-maintenance issue, bounty, competition task, pilot, or similar opportunity can look valuable while still be a bad use of premium model/tool time because the acceptance authority is unclear, payout route is unproven, KYC is blocked, the reward is noncash, evidence is stale, the deadline is gone, another worker already owns the lane, or tool cost destroys the economics.

The module composes the existing canonical-source-hardened `concierge.fleet_economic_admission` compiler rather than replacing it. New intake first passes `concierge.paid_work_dollar_floor`: only verified USD cash at $50+ proceeds here, while $10–49 is save-up inventory and <$10 is pruned.

## Dispositions

The gate emits exactly one decision:

| Decision | Meaning |
|---|---|
| `GO` | Fresh ordinary-cash work clears value, account, deadline, congestion, gross-economics, and post-tool-cost floors. |
| `HOLD_VALUE_UNKNOWN` | Value cannot be compared without inventing a conversion or missing cost/value evidence. |
| `HOLD_ACCOUNT_GATE` | Acceptance, payout route, KYC/account readiness, coordination evidence, or its freshness is insufficient. |
| `SKIP_ECONOMICS` | A current terminal condition makes the work uneconomic: deadline, saturation, gross economics, or post-cost economics. |

A `GO` is an **internal admission signal only**. It does not claim a task, send a submission, send outreach, prove payment, move money, or create revenue authority.

## Non-negotiable value custody

The gate does not perform FX and does not assign cash value to project tokens, credits, points, or other noncash units.

A `25 RTC` task may be interesting, but without an authorized valuation it is `HOLD_VALUE_UNKNOWN`, not `$X`, and not an economic `GO`.

Likewise, if model/tool cost is unknown or is denominated in a different unit from the cash payout, the decision is `HOLD_VALUE_UNKNOWN`.

## Economic composition

For ordinary cash with known same-currency tool cost:

1. The **gross advertised reward** is passed through `fleet_economic_admission` using the same canonical source URL and estimated engineering hours.
2. If the existing compiler says the single item is not economically eligible, the result is `SKIP_ECONOMICS`.
3. The gate subtracts known model/tool cost exactly with `Decimal`.
4. The resulting net reward and net reward/hour must still clear the existing currency's `min_single_reward` and `min_reward_per_agent_hour` floors.
5. Only then can account gates participate in a final `GO`.

This keeps the existing economics policy authoritative while adding the missing post-cost boundary.

## Decision precedence

The gate collects separate terminal, value, and account reasons.

1. Terminal deadline/congestion/economic reasons -> `SKIP_ECONOMICS`.
2. Otherwise, uncertain value -> `HOLD_VALUE_UNKNOWN`.
3. Otherwise, account/acceptance/payout/coordination blockers -> `HOLD_ACCOUNT_GATE`.
4. Otherwise -> `GO`.

This means an expired `$1` task is skipped instead of asking an operator to solve KYC first. Conversely, a live noncash task is held rather than being evaluated through an invented conversion.

## Evidence freshness

The policy sets one bounded `evidence_max_age_seconds`. The gate applies it to:

- advertised payout observation;
- claim/congestion observation;
- first-party acceptance evidence;
- payout-route evidence;
- account/KYC evidence.

Future-dated evidence is rejected as malformed input. Stale payout evidence holds value. Stale operational/account evidence holds the account gate.

## Claim congestion

`max_active_claims` is a hard single-writer/economic-capacity threshold. Fresh `active_claims >= max_active_claims` produces `SKIP_ECONOMICS`.

A stale congestion observation never authorizes work; it becomes `HOLD_ACCOUNT_GATE`.

This is deliberately compatible with the fleet's separate Muse/single-writer coordination for outbound communication. This module does not send or reserve outbound messages.

## Request schema

```json
{
  "schema": "paid-work-effort-value-gate/v1",
  "as_of": "2026-09-16T20:00:00Z",
  "policy": {
    "schema": "paid-work-effort-value-policy/v1",
    "evidence_max_age_seconds": 86400,
    "deadline_safety_seconds": 3600,
    "max_active_claims": 1,
    "fleet_economic_policy": {
      "schema": "fleet-economic-policy/v1",
      "min_batch_items": 2,
      "max_batch_items": 200,
      "currencies": {
        "USD": {
          "min_single_reward": "50",
          "min_batch_reward": "500",
          "min_reward_per_agent_hour": "100"
        }
      }
    }
  },
  "candidate": {
    "work_id": "paid-task-123",
    "canonical_source_url": "https://example.test/work/123",
    "advertised_payout": {
      "amount": "300",
      "currency": "USD",
      "unit_type": "CASH",
      "observed_at": "2026-09-16T19:30:00Z"
    },
    "estimated_engineering_hours": "1",
    "model_tool_cost": {
      "state": "KNOWN",
      "amount": "10",
      "currency": "USD"
    },
    "deadline_at": "2026-09-17T20:00:00Z",
    "congestion": {
      "active_claims": 0,
      "observed_at": "2026-09-16T19:30:00Z"
    },
    "acceptance": {
      "state": "CONFIRMED",
      "authority": "FIRST_PARTY",
      "evidence_url": "https://evidence.example/acceptance",
      "observed_at": "2026-09-16T19:30:00Z"
    },
    "payout_route": {
      "state": "CONFIRMED",
      "evidence_url": "https://evidence.example/payout-route",
      "observed_at": "2026-09-16T19:30:00Z"
    },
    "account_kyc": {
      "state": "READY",
      "evidence_url": "https://evidence.example/account-kyc",
      "observed_at": "2026-09-16T19:30:00Z"
    }
  }
}
```

All timestamps are exact UTC second timestamps ending in `Z`. Money and effort are exact decimal strings or integers; JSON floats, booleans, NaN, and infinity are rejected. Critical request objects reject unknown fields rather than silently expanding authority.

## Account states

Acceptance:

- `CONFIRMED`
- `UNKNOWN`
- `REJECTED`

Acceptance must also declare `authority: FIRST_PARTY`; `OTHER` and `UNKNOWN` cannot produce `GO`.

Payout route:

- `CONFIRMED`
- `UNKNOWN`
- `BLOCKED`

Account/KYC:

- `READY`
- `NOT_REQUIRED`
- `UNKNOWN`
- `BLOCKED`

`NOT_REQUIRED` still requires fresh evidence establishing that state.

## Tool cost

Known cost:

```json
{"state": "KNOWN", "amount": "12.50", "currency": "USD"}
```

Unknown cost:

```json
{"state": "UNKNOWN"}
```

Unknown cost must not also assert an amount or currency.

## Policy

The checked-in fleet policy for this gate is `policies/paid_work_effort_value_v1.json`.

It embeds the existing `fleet-economic-policy/v1` thresholds so the gate's post-cost checks use the same single-item reward and reward/hour floors.

Changing either this policy or the underlying fleet economics implementation re-runs the dedicated integration workflow.

## Receipts

Every successful compilation emits:

- canonical source identity;
- request and policy SHA-256 commitments;
- exact decision and reason codes;
- value/account/deadline/congestion gate observations;
- the nested fleet-economics receipt when value is comparable;
- authority ceilings;
- an outer SHA-256 receipt commitment.

`verify_receipt()` checks both the outer commitment and the nested fleet-economics receipt when present.

## CLI

Human summary:

```bash
python -m concierge.paid_work_effort_value_gate request.json
```

Full receipt:

```bash
python -m concierge.paid_work_effort_value_gate request.json --json
```

The CLI uses bounded UTF-8 JSON parsing with duplicate-key rejection.

## Seed fixtures

Two synthetic fixtures preserve the motivating economics without pretending to be live evidence:

- `data/paid_work_effort_value_gate/frantic_one_dollar.json` -> `SKIP_ECONOMICS`
- `data/paid_work_effort_value_gate/rustchain_25_rtc_unknown_value.json` -> `HOLD_VALUE_UNKNOWN`

The RustChain-shaped fixture demonstrates the noncash rule: 25 RTC is not silently converted into USD.

## Authority ceiling

This gate never provides:

- external claim authority;
- outbound-message authority;
- submission authority;
- payment/cash/revenue authority;
- FX authority;
- noncash valuation authority.

Those actions require their own evidence and control planes.


## Mandatory dollar-floor ancestry

For new swarm bounty work, this gate now constructs and semantically verifies a
`paid-work-dollar-floor/v2` receipt from the candidate's payout evidence before
gross fleet economics can run. Only `ACTIVE_REVIEW` ancestry can reach the
downstream economic calculation. `PILE_SAVE_UP` and
`PRUNE_BELOW_FLOOR` become terminal economic skips; unverified, ceiling,
cross-boundary range, noncash, and non-USD dollar-floor states remain value
holds.

When a verified RANGE is wholly above the active threshold, downstream
profitability uses its guaranteed minimum, never its maximum. The complete
dollar-floor receipt is embedded in the effort/value receipt and reverified by
`verify_receipt()`; claim admission's deterministic request replay therefore
inherits the same source-owned floor automatically.

