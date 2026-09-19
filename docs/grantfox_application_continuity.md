# GrantFox application continuity ledger

`concierge.grantfox_application_continuity` is the durable companion to the
point-in-time `grantfox_queue_gate`.

The queue gate answers whether a **fresh snapshot** is eligible to apply, wait,
or implement. The continuity ledger answers whether the swarm has **already**
crossed an irreversible lifecycle boundary and therefore must not repeat it.

It is advisory-only. It never applies to an issue, posts a comment, creates or
submits a PR, changes assignment, adjudicates a bounty, or moves funds.

## Why this exists

High-throughput workers can observe contradictory snapshots:

- an application receipt exists, but a later page renders `Applied = false`;
- the actor was assigned, but a later provider snapshot temporarily renders no
  assignee;
- a submission PR exists, while a later snapshot links a different PR;
- a merged PR is mistaken for sponsor approval or payment.

The ledger makes those contradictions explicit instead of silently downgrading
state.

## Event sequence

A request binds to one verified `grantfox-queue-gate/v1` receipt and a strictly
time-ordered event list:

1. `SNAPSHOT` — read-only provider observation. It can reveal conflicts but
   cannot erase durable receipts.
2. `APPLICATION_RECEIPT` — durable evidence that the actor applied.
3. `ASSIGNMENT_RECEIPT` — durable evidence that GrantFox/provider assigned the
   configured actor.
4. `SUBMISSION_RECEIPT` — exact PR URL for the assigned work.
5. `ADJUDICATION_RECEIPT` — explicit `APPROVED` or `REJECTED` provider result.
   Optional amount/currency on approval is treated as an approved amount, not
   payment.
6. `PAYMENT_RECEIPT` — explicit `SENT` or `RECEIVED`, amount/currency, external
   receipt URL, and receipt reference.

Receipts are monotonic. Re-using a durable receipt URL, swapping issue identity,
timestamp rollback, assigning a different actor, changing the submission PR,
paying before approval, or regressing `RECEIVED` to `SENT` fails closed.

Snapshot regressions do not erase history. They produce `HOLD_RECONCILE`, for
example `SNAPSHOT_APPLICATION_REGRESSION` or
`SNAPSHOT_ASSIGNMENT_REGRESSION`.

## Lifecycle states

`DISCOVERED -> APPLIED -> ASSIGNED -> SUBMITTED -> APPROVED -> PAYMENT_SENT -> PAID`

`REJECTED` is a terminal adjudication alternative.

A merged PR alone is not modeled as approval and cannot mint `PAID`.

## CLI

```bash
python -m concierge.grantfox_application_continuity ledger.json --json
```

Exit code `2` means durable evidence conflicts and must be reconciled. Exit code
`0` means the receipt is internally consistent; the output still carries no
provider mutation authority.

Every result retains a producer-evidence envelope containing the exact verified
queue receipt, configured actor, and input event sequence. Verification does not
trust the continuity receipt's SHA-256 alone: `verify_continuity_receipt()`
re-runs the queue verifier, replays the lifecycle compiler from that retained
evidence, and requires exact equality with the supplied receipt.

This closes summary-rehash promotion: changing an `APPLIED` or `DISCOVERED`
receipt to `ASSIGNED`, even with a correctly recomputed outer digest, fails
unless the retained producer evidence itself compiles to that state. Receipts
from the former self-hash-only format intentionally fail the repaired verifier
and must be regenerated.

The retained evidence is still an advisory record, not a provider signature.
External assignment, adjudication, and payment authenticity must continue to
come from the provider/GitHub/receipt sources represented by the evidence.
