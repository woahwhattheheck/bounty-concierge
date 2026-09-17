# Paid-work implementation dispatch gate

`concierge.revenue_dispatch` is the authoritative implementation-dispatch seam for paid GitHub work. Live revenue intake and maintainer availability remain necessary, but they are not sufficient to start engineering work.

A candidate can emit `dispatch=true` only when all three layers clear:

1. canonical live revenue intake is dispatchable;
2. maintainer availability has no stable terminal/filled/cancelled signal; and
3. an exact `paid-work-effort-value-gate-receipt/v1` verifies and returns `GO` for the same work identity, canonical source, expected policy, receipt bytes, and explicit dispatch decision time.

A missing economic receipt is an explicit `ECONOMICS:GATE_RECEIPT_REQUIRED` HOLD, not backward-compatible authorization. Upstream intake or availability HOLDs short-circuit before economic verification so a lower layer cannot promote an already blocked candidate.

## Exact evidence binding

Promotion now binds five independent identities instead of trusting a parsed receipt object alone:

- `work_id` must equal the receipt work identity;
- canonical source URL must equal the live-intake canonical source;
- `--gate-receipt-sha256` must equal SHA-256 of the **exact receipt file bytes**;
- `--policy-sha256` must equal the receipt's `policy_sha256` and is supplied independently by the caller;
- `--dispatch-as-of` is an explicit canonical UTC timestamp used for freshness instead of ambient wall-clock time.

The verified result also binds the receipt self-digest and request digest. These fields are hashed into `economic_admission.binding_sha256` using schema `paid-work-dispatch-evidence-binding/v1`. The same upstream result, receipt bytes, expected digests, dispatch time, and age policy produce the same binding. This is an audit/evidence receipt, not a payment or revenue receipt.

The default economic-receipt age window is 3,600 seconds. A caller may tighten or relax it up to 86,400 seconds, but booleans/invalid bounds are rejected. A receipt after `dispatch_as_of` fails. A stale receipt fails. A mismatched byte digest, policy digest, source, or work identity fails. Tampering fails.

Receipt JSON is size-bounded, strict UTF-8, and rejects BOMs, duplicate keys, floating-point numbers, and non-finite constants before the gate verifier runs. The exact upstream authority object is also schema-bound: an unreviewed extra authority key fails closed rather than being silently accepted.

`HOLD_VALUE_UNKNOWN`, `HOLD_ACCOUNT_GATE`, and `SKIP_ECONOMICS` remain non-dispatchable, but their verified evidence binding is retained in the HOLD result for auditability.

## Authority boundary

A verified `GO` authorizes **internal implementation only**. The resulting `dispatch_authority` keeps these false:

- `external_claim_authority`
- `external_submission_authority`
- `payment_cash_or_revenue_authority`

The dispatch seam does not claim an issue, contact a maintainer, submit a patch, prove an award, prove payout, or recognize revenue. Those require their own evidence and authority paths.

## CLI

The CLI requires the exact work identity, exact receipt file, independently expected receipt-byte digest, independently expected policy digest, and an explicit dispatch decision timestamp:

```bash
python -m concierge.revenue_dispatch OWNER/REPO ISSUE \
  --work-id 'OWNER/REPO#ISSUE' \
  --gate-receipt /path/to/paid-work-gate-receipt.json \
  --gate-receipt-sha256 <sha256-of-exact-file-bytes> \
  --policy-sha256 <expected-policy-sha256> \
  --dispatch-as-of 2026-09-16T20:05:00Z \
  --json
```

The caller should obtain the expected byte and policy digests from the orchestration/evidence record that selected the gate artifact; deriving them from an untrusted file at the same call site defeats independent binding.

## Regression coverage

`tests/test_revenue_dispatch.py` composes one real upstream paid-work gate receipt into the authoritative dispatch seam and also covers missing receipt, GO, every non-GO decision, exact-byte mismatch, policy mismatch, work/source mismatch, explicit-time stale/future behavior, deterministic replay, tamper, authority amplification and schema extension, invalid age bounds, BOM/duplicate-key/float rejection, and upstream short-circuit behavior.

The existing `paid-work-effort-value-gate.yml` workflow is path-scoped to this module/test/doc and runs the paid-work gate, dispatch composition, and fleet-economics suites under normal and `python -O` modes on Python 3.9 and 3.13, followed by syntax compilation.
