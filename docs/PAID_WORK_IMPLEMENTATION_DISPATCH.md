# Paid-work implementation dispatch gate

`concierge.revenue_dispatch` is the implementation-dispatch seam for paid GitHub work. Live revenue intake and maintainer availability remain necessary, but they are no longer sufficient to start engineering work.

A candidate can emit `dispatch=true` only when all three layers clear:

1. canonical live revenue intake is dispatchable;
2. maintainer availability has no stable terminal/filled/cancelled signal; and
3. a current `paid-work-effort-value-gate-receipt/v1` verifies and returns `GO` for the exact work identity and exact canonical source URL.

The default economic-receipt dispatch window is 3,600 seconds. A caller may tighten or relax it up to 86,400 seconds, but boolean/invalid bounds are rejected. Future receipts fail. Stale receipts fail. Tampered receipts fail. A valid receipt for a different `work_id` or canonical source fails. `HOLD_VALUE_UNKNOWN`, `HOLD_ACCOUNT_GATE`, and `SKIP_ECONOMICS` remain non-dispatchable.

A missing economic receipt is an explicit `ECONOMICS:GATE_RECEIPT_REQUIRED` HOLD, not backward-compatible authorization. Upstream intake or availability HOLDs short-circuit before economic verification so a lower layer cannot promote an already blocked candidate.

## Authority boundary

A verified `GO` authorizes **internal implementation only**. The resulting `dispatch_authority` keeps these false:

- `external_claim_authority`
- `external_submission_authority`
- `payment_cash_or_revenue_authority`

The dispatch seam does not claim an issue, contact a maintainer, submit a patch, prove an award, prove payout, or recognize revenue. Those require their own evidence and authority paths.

## CLI

The CLI now requires both the exact work identity and a gate-receipt file:

```bash
python -m concierge.revenue_dispatch OWNER/REPO ISSUE \
  --work-id 'OWNER/REPO#ISSUE' \
  --gate-receipt /path/to/paid-work-gate-receipt.json \
  --json
```

Receipt JSON is size-bounded, UTF-8 only, and duplicate keys are rejected before the gate verifier runs.

## Regression coverage

`tests/test_revenue_dispatch.py` covers the missing-receipt bypass, verified fresh GO, every non-GO decision, tamper, source/work mismatch, stale/future receipt, authority amplification, invalid age bounds, duplicate JSON keys, and short-circuit behavior. The paid-work workflow runs both the admission primitive and dispatch composition under normal and `python -O` modes on Python 3.9 and 3.13.
