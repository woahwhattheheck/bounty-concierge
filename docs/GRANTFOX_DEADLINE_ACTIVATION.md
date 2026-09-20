# GrantFox deadline activation

## Purpose

A GrantFox issue can remain OPEN after its sponsor or campaign deadline expires. The existing lifecycle and live-cash gates therefore need one final current-deadline check before work is treated as actionable.

`concierge.grantfox_deadline_activation` composes:

- a current `grantfox-live-cash-activation-receipt/v1`; and
- a semantic `bounty-deadline-gate/v1` receipt for the same GitHub issue.

## Current-time rule

The deadline receipt is not accepted only because it was green when first created. The compositor rebuilds the deadline request from its captured evidence and evaluates it with the process UTC clock at composition time.

Actionable GrantFox states are preserved only when the recomputed deadline disposition is `DEADLINE_CURRENT`.

The following remain fail-closed:

- elapsed sponsor deadlines;
- stale issue or deadline observations;
- stale, invalid, or retrograde extensions;
- closed canonical issues; and
- ambiguous date-only cutoff boundaries.

An issue or marketplace row remaining OPEN does not itself extend a deadline.

## Identity and precedence

The live-cash activation and deadline receipts must identify the exact same `owner/repo#issue`.

Existing upstream holds remain dominant. A deadline receipt cannot make a provider, source, dependency, lifecycle, or economics hold actionable.

## Freshness

Composite receipts live for at most 300 seconds. Verification reproduces the exact composition at `composed_at` and then checks the captured deadline evidence again at the verifier's current UTC clock. A cutoff crossed during that five-minute window therefore invalidates an actionable receipt.

## Authority

This gate is advisory-only. It does not grant provider-application, implementation-write, repository-write, submission, adjudication, external-contact, or payout authority.

## CLI

```bash
python -m concierge.grantfox_deadline_activation request.json --json
```

Request:

```json
{
  "schema": "grantfox-deadline-activation/v1",
  "live_cash_activation_receipt": {"...": "..."},
  "deadline_receipt": {"...": "..."}
}
```

Run this compositor after the live-cash activation gate using deadline evidence that is still inside the deadline gate's configured observation-age limit.
