# GrantFox live-cash activation gate

## Problem

The GrantFox execution path has strong but separate controls:

- `grantfox_activation_gate` composes provider queue state, source readiness,
  dependency readiness/fulfillment, and application lifecycle continuity.
- `bounty_live_cash_admission` re-reads canonical GitHub state and admits only
  fixed USD economics, routing `$50+` to `main_bounty_queue`, `$10-49` to the
  saving pile, and non-fixed/unverified/stale work to HOLD/REJECT.

Without a final composition step, a lifecycle receipt can be operationally
actionable while the same issue's economics are unverified, below the active
floor, or no longer live.

## Contract

`concierge.grantfox_live_cash_activation` consumes:

1. a semantically valid `grantfox-activation-gate/v1` receipt; and
2. a `bounty-live-cash-admission-receipt/v1` receipt that re-verifies against
   canonical GitHub **at composition time**.

The receipts must identify the exact same `owner/repo#issue`.

An active GrantFox lifecycle disposition is preserved only when the live cash
receipt simultaneously proves all of:

- `disposition == ACTIVE_REVIEW`;
- `route == main_bounty_queue`;
- `currency == USD`;
- `fixed_semantics == true`; and
- `fixed_amount >= 50`.

`$10-49` receipts therefore remain in `bounty_pile_10_49`; they cannot be
promoted into active GrantFox work. Non-fixed rewards, token rewards, stale
receipts, identity mismatches, and canonical source changes fail closed.

GrantFox lifecycle HOLD states dominate economics: cash cannot make a blocked
provider/source/dependency/lifecycle path actionable.

## Live verification

`verify_live_cash_activation_receipt` is intentionally not historical
self-replay. It recompiles the composition and calls
`verify_live_cash_receipt`, which re-reads canonical GitHub. If the issue
closes, reward semantics change, source generation changes, or saturation makes
the underlying cash receipt no longer current, verification fails.

This makes a saved green receipt unsuitable as permanent dispatch authority.

## Authority

The gate is advisory-only. It grants no authority to:

- apply to a provider;
- implement assignment-gated work;
- submit or merge upstream code;
- contact a sponsor;
- adjudicate a reward; or
- move funds or modify a wallet.

Those actions remain controlled by their existing provider/repository/payout
boundaries.

## CLI

```bash
python -m concierge.grantfox_live_cash_activation request.json --json
```

The request schema is:

```json
{
  "schema": "grantfox-live-cash-activation/v1",
  "activation_receipt": {"...": "..."},
  "live_cash_receipt": {"...": "..."}
}
```

Exit status is `0` only for preserved non-HOLD lifecycle dispositions and `2`
for `HOLD_ECONOMICS` / `HOLD_GRANTFOX_ACTIVATION` or malformed input.
