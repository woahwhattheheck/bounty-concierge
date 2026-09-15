# Single-writer payout delivery gate

`concierge.payout_delivery_gate` is the last offline safety boundary before a human-selected operator performs one external payout-claim or settlement follow-up mutation. It exists to prevent two agents from contacting the same sponsor route for the same paid-work opportunity seconds apart.

The gate **never sends anything**. It accepts three exact evidence packets: a payout-delivery request, an arbitration/selection lease, and a complete route-scoped prior-mutation history capture. It deterministically emits a receipt whose strongest possible state is `READY_FOR_ONE_PROVIDER_MUTATION`.

## Request

The request binds an opaque provider-route SHA-256, exact work URL and 40-hex head, opportunity reference, reward reference, claimant label, exact source-authority reference and digest, the SHA-256 of the intended message bytes, and a preparation timestamp. The cleartext email address, DM identity, form target, or comment route does not need to be persisted in this artifact.

## Arbitration lease

An arbitration record must bind the exact request digest and provider-route digest, one owner seat, one arbiter, a durable HTTPS arbitration receipt/reference, and an issuance/expiry window. `NOT_SELECTED` and `REVOKED` never authorize a provider mutation. Selection is valid only from `issued_at` inclusive until `expires_at` exclusive.

This supports a Muse-style single-writer workflow without giving this module any power to request or perform that arbitration itself.

## Complete route history

The history capture must explicitly say `complete=true`, bind the same request and provider route, and be captured after the selection. Every prior mutation row must belong to that provider route. A `CONFIRMED_SENT` or `AMBIGUOUS` row blocks another mutation when it belongs to the same request **or** the same opportunity, even if an earlier agent used a different request identifier or different message bytes.

`CONFIRMED_NOT_SENT` does not block a later attempt. A terminal `SETTLED_PAID`, `SPONSOR_DNR`, or `OPPORTUNITY_CLOSED` marker blocks outreach entirely. A complete history older than 600 seconds is treated as stale; exact age 600 seconds is accepted.

## Authority states

A clean packet can emit `READY_FOR_ONE_PROVIDER_MUTATION`. Other states include `DUPLICATE_SEND_BLOCKED`, `SETTLED_NO_SEND`, `HISTORY_INCOMPLETE_HOLD`, `ARBITRATION_REQUIRED`, `ARBITRATION_REVOKED`, `LEASE_NOT_YET_ACTIVE`, `LEASE_EXPIRED`, `HISTORY_PREDATES_LEASE_HOLD`, and `HISTORY_STALE_HOLD`.

Even in the ready state, the receipt keeps these facts false: external action performed, sponsor receipt/acceptance inferred, merge inferred, settlement inferred, payment inferred, cash inferred, and revenue inferred. The receipt is an offline owner-control authorization only.

## CLI

```bash
concierge-revenue payout-delivery compile request.json arbitration.json history.json --output delivery-receipt.json
concierge-revenue payout-delivery verify request.json arbitration.json history.json delivery-receipt.json --output verified.json
```

Inputs use strict JSON with duplicate-key rejection, a 1 MiB bound, regular-file and no-follow leaf checks. Outputs are create-exclusive and refuse overwrite or symlink traversal at **every parent component and the leaf**. Output creation is anchored to verified directory descriptors. After a leaf has been created, any later write or fsync failure deliberately preserves the created pathname instead of attempting pathname cleanup: another same-authority writer may already have replaced that leaf, so unlink-on-error could delete a foreign successor. Failed output paths therefore require explicit operator reconciliation before retry. Relative output paths containing `..` are rejected rather than allowing the output authority root to move during traversal.

The hardened output boundary fails closed on hosts that cannot provide `O_NOFOLLOW`, `O_DIRECTORY`, and `dir_fd` support. This is intentional: silently falling back to leaf-only no-follow semantics would reintroduce parent-symlink redirection.

Verification recompiles at the receipt's exact `as_of` timestamp and requires byte-equivalent canonical JSON semantics.

Synthetic fixtures under `data/payout_delivery_gate/` are test vectors only. They do not constitute a live sponsor claim, a Muse selection, a payment request, or evidence that any sponsor was contacted.
