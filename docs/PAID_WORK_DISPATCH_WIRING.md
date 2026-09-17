# Paid-work economics dispatch wiring

`concierge.revenue_dispatch` is the last read-only authorization seam before a paid-work candidate is allowed into new implementation work. It now composes three independent gates in order:

1. **Canonical live intake** — reward/provenance/competition/assignment evidence must already be dispatchable.
2. **Paid-work effort/value gate** — the retained request and receipt must verify, recompile exactly, bind to the same `owner/repo#issue` work ID and canonical source URL, and return `GO`.
3. **Maintainer availability** — only after a verified economics `GO`, the canonical thread is checked for a stable outcome that would make new work stale or redundant.

A failure at an earlier gate stops later provider reads. This is deliberate load shedding: `HOLD_VALUE_UNKNOWN`, `HOLD_ACCOUNT_GATE`, `SKIP_ECONOMICS`, missing evidence, tampering, or cross-candidate replay cannot burn implementation capacity and do not trigger the availability read.

## Binding contract

For `repo=acme/widgets` and issue `17`, the paid-work request and compiled receipt must both carry:

```text
work_id = acme/widgets#17
canonical_source_url = <exact canonical_source_url from live intake>
```

The wrapper calls `paid_work_effort_value_gate.verify_receipt(receipt)`, recompiles the retained request, and requires exact receipt equality. A self-consistent receipt for another work item therefore cannot be replayed onto this dispatch candidate.

The receipt integrity digest is an internal evidence-integrity mechanism, not a cryptographic sponsor signature. Provider authenticity remains the responsibility of the canonical intake and the evidence adapters used to build the paid-work request.

## Decisions

- `GO`: proceed to the maintainer availability guard. Dispatch is authorized only if that guard also clears.
- `HOLD_VALUE_UNKNOWN`: fail closed with `HOLD`; do not perform the availability read.
- `HOLD_ACCOUNT_GATE`: fail closed with `HOLD`; do not perform the availability read.
- `SKIP_ECONOMICS`: fail closed with `REJECT`; do not perform the availability read.
- missing, invalid, mismatched, or unsupported evidence: fail closed with `HOLD`; do not perform the availability read.

The wrapper preserves an upstream `REJECT` and never upgrades a non-dispatchable intake.

## CLI

The dispatch CLI now requires the exact retained economics request and compiled receipt:

```bash
python -m concierge.revenue_dispatch OWNER/REPO ISSUE \
  --paid-work-gate-request request.json \
  --paid-work-gate-receipt receipt.json \
  --json
```

Both files are size-bounded JSON objects. Duplicate keys and non-finite JSON constants are rejected. Exit status remains `0` for dispatchable, `2` for `HOLD`, and `3` for other non-dispatch outcomes such as `REJECT`.

This seam is read-only: it does not claim work, post comments, submit code, contact sponsors, mutate payment rails, or make payment/revenue assertions.
