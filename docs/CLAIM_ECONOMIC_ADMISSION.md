# Claim economics admission

The installed `concierge claim` path is an **instruction-emission** boundary, not an external claim action. It prints the exact GitHub issue and comment text a human or separately authorized sender could use; it does not post that comment itself.

Because those instructions can still start expensive speculative work, live claim instructions fail closed unless four independent layers agree:

1. a verified payoff bundle binds a current `BOUNTY / SUBMIT_WORK` path to the exact GitHub issue and retains bounded speculative-work budget;
2. a verified `paid_work_effort_value_gate` receipt for the same payoff-derived `work_id` and exact issue URL returns `GO`;
3. canonical bounty preflight remains dispatchable; and
4. maintainer availability remains clear.

The economics check runs **before provider reads**. A missing, stale, future-dated, tampered, source-mismatched, work-mismatched, policy-mismatched, non-`GO`, or authority-amplified receipt prevents the wrapper from spending live preflight/availability reads.

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
  --economic-receipt /path/to/paid-work-receipt.json \
  --economic-receipt-sha256 <sha256-of-exact-receipt-bytes> \
  --economic-policy-sha256 <independently-expected-policy-sha256> \
  --economic-as-of 2026-09-17T20:30:00Z
```

`--economic-as-of` is explicit canonical UTC at second precision. The receipt may be at most 3,600 seconds old and may not be from the future.

The expected receipt-byte digest and policy digest must come from the orchestration/evidence record that selected the artifact. Deriving either expected value from the same untrusted file at the call site defeats independent binding.

## Failure behavior

All economics failures exit closed before live bounty/preflight provider reads. JSON mode returns only a safe error class and reason code; source document text and receipt contents are not echoed.

A verified success returns an internal proof bound to:

- repository + issue;
- payoff-derived work ID;
- exact canonical issue URL;
- exact receipt bytes;
- receipt self-digest;
- request digest;
- policy digest;
- receipt decision time and explicit claim decision time;
- receipt age policy;
- exact `GO` decision.

The proof authority string is `INTERNAL_CLAIM_INSTRUCTION_ADMISSION_ONLY_NO_EXTERNAL_ACTION`.
