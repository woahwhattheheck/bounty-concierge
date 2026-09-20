# Claim economics admission

The installed `concierge claim` path is an **instruction-emission** boundary, not an external claim action. It prints the exact GitHub issue and comment text a human or separately authorized sender could use; it does not post that comment itself.

Because those instructions can still start expensive speculative work, live claim instructions fail closed unless four independent layers agree:

1. a verified payoff bundle binds a current `BOUNTY / SUBMIT_WORK` path to the exact GitHub issue and retains bounded speculative-work budget;
2. a verified `paid_work_effort_value_gate` receipt for the same payoff-derived `work_id` and exact issue URL returns `GO`;
3. source-bound `bounty_live_cash_admission` re-reads canonical GitHub state and returns `ACTIVE_REVIEW / main_bounty_queue` under the owner-owned USD $50 active / $10 pile floor;
4. canonical bounty preflight remains dispatchable; and
5. maintainer availability remains clear.

The payoff and economics checks run **before provider reads**. The first provider-backed authority is the source-bound live-cash admission gate; its reward amount, currency, and evidence authority are derived from canonical GitHub state rather than caller fields. Live admission reads one retained paid-work request and its receipt through stable regular-file descriptors, re-runs the existing paid-work gate, and requires the supplied receipt to equal that deterministic replay. A missing, stale, future-dated, tampered, source-mismatched, work-mismatched, policy-mismatched, non-`GO`, or authority-amplified artifact prevents the wrapper from spending live preflight/availability reads.

## Economics remains single-source

`concierge.claim_economic_admission` does not price tokens, perform FX, estimate work, or compile a second economic opinion. It only verifies and binds the existing `paid_work_effort_value_gate` receipt.

Therefore the existing semantics carry through unchanged:

- ordinary cash with known same-currency model/tool cost can become `GO` only after all value/account/deadline/congestion floors clear;
- noncash or unvalued units remain `HOLD_VALUE_UNKNOWN`;
- unknown model/tool cost remains `HOLD_VALUE_UNKNOWN`;
- tiny, expired, saturated, or post-cost-negative work becomes `SKIP_ECONOMICS`;
- blocked acceptance/payout/KYC evidence becomes `HOLD_ACCOUNT_GATE`.

A gate `GO` remains an internal admission signal. It is **not** external claim authority, submission authority, payment authority, cash evidence, or revenue evidence.

## Live CLI

The wrapper-owned evidence options are intentionally stripped before the legacy argparse surface receives the command:

```bash
concierge claim \
  --repo OWNER/REPO \
  --issue 42 \
  --wallet WALLET \
  --payoff-bundle /path/to/payoff-bundle \
  --economic-request /path/to/paid-work-request.json \
  --economic-receipt /path/to/paid-work-receipt.json
```

Live claim freshness is evaluated against the wrapper's process-owned UTC clock at second precision; callers cannot rewind it with a CLI option. The replayed receipt may be at most 3,600 seconds old and may not be from the future.

The checked-in paid-work policy digest is source-owned by this admission generation. A caller-supplied weaker policy is rejected even if it deterministically compiles a `GO`. The request and receipt remain local evidence, but the receipt cannot self-authorize by merely recomputing its own integrity hash: admission recompiles the request through the existing single valuation engine and requires exact canonical equality with the retained receipt.

## Failure behavior

All economics failures exit closed before live bounty/preflight provider reads. JSON mode returns only a safe error class and reason code; source document text and receipt contents are not echoed.

A verified success returns an internal proof bound to:

- repository + issue;
- payoff-derived work ID;
- exact canonical issue URL;
- exact request bytes and exact receipt bytes;
- receipt self-digest;
- request digest;
- policy digest;
- receipt decision time and explicit claim decision time;
- receipt age policy;
- exact `GO` decision.

The proof authority string is `INTERNAL_CLAIM_INSTRUCTION_ADMISSION_ONLY_NO_EXTERNAL_ACTION`.
