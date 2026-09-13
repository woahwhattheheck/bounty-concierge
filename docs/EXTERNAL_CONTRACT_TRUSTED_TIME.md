# External contract trusted-time boundary

External-contract qualification and proposal assembly are current-state decisions. Listing freshness, listing close time, and availability-evidence freshness therefore depend on **verifier-owned current UTC**, not on a timestamp supplied by the opportunity packet or by a CLI caller.

## Production rule

The production entry points are:

- `qualify_external_contract_current(snapshot)`
- `verify_contract_qualification_receipt_current(snapshot, receipt)`
- `build_external_contract_proposal_current(snapshot, receipt, brief)`
- `python -m concierge.contract_qualification ...`
- `python -m concierge.contract_proposal ...`

The two CLIs intentionally expose **no `--as-of` option**. They capture current UTC inside the process immediately before evaluation. A user cannot backdate the CLI to make an expired listing, a stale listing observation, or stale availability evidence appear current.

## Explicit-time primitives

`qualify_external_contract(..., as_of=...)`, `verify_contract_qualification_receipt(..., as_of=...)`, and `build_external_contract_proposal(..., as_of=...)` remain available for deterministic tests, historical reconstruction, and verifier code that already owns a trusted clock. The timestamp passed to those primitives is part of the caller's trust boundary. It must not come from marketplace data, proposal data, an HTTP request parameter, or any other untrusted request surface.

Code that needs a live commercial decision should use the `*_current` entry points instead.

## Why this is a security/revenue boundary

A qualification receipt records the time at which its evidence was evaluated. That historic fact does not authorize a later action forever. Before a receipt can support an owner-review proposal, the same snapshot is re-evaluated at current verifier time. If the listing deadline has passed, the listing observation exceeded its TTL, or required availability evidence is stale, current verification fails closed.

This remains a decision-support boundary only. It does not submit a proposal, contact a customer, accept terms, complete KYC, spend, accept a contract, infer an award/payment, or recognize revenue.
