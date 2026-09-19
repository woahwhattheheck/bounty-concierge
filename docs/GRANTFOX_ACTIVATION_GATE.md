# GrantFox activation gate

`concierge.grantfox_activation_gate` composes the four independent GrantFox
control receipts that workers otherwise have to interpret separately:

1. `grantfox-queue-gate/v1` — current provider queue/application state.
2. the verified GrantFox source-readiness receipt — pinned repository/source state.
3. `grantfox-dependency-fulfillment-receipt/v1` — verified prerequisite readiness plus fresh default-branch landing evidence, including explicit zero-prerequisite fulfillment.
4. `grantfox-application-continuity/v1` — monotonic durable lifecycle evidence.

The gate is intentionally advisory-only. It performs no provider application,
assignment, GitHub write, submission, adjudication, sponsor contact, payment, or
wallet action.

## Why a compositor is necessary

Each underlying receipt is useful on its own, but no one receipt proves that all
four views agree. In a fast swarm, these are materially different facts:

- source code can be perfectly aligned while the actor is only **APPLIED**;
- a fresh queue page can render implementation-eligible while durable lifecycle
  evidence identifies a conflicting actor or older application state;
- an assignment can be durable while the issue's source references have drifted;
- a submitted, approved, or paid issue must never be accidentally reactivated as
  implementation work by a later point-in-time snapshot.

The activation gate fails closed across those seams.

## Input

```json
{
  "schema": "grantfox-activation-gate/v1",
  "queue_receipt": {"...": "verified grantfox-queue-gate/v1 receipt"},
  "source_receipt": {"...": "verified GrantFox source-readiness receipt"},
  "fulfillment_receipt": {"...": "verified GrantFox dependency-fulfillment receipt"},
  "continuity_receipt": {"...": "verified grantfox-application-continuity/v1 receipt"}
}
```

All four receipts must verify using their native verifier. The gate then binds:

- case-insensitive owner/repository + exact issue number;
- exact actor between queue and continuity;
- the source receipt's embedded queue receipt byte-for-byte to the supplied queue
  receipt;
- the fulfillment receipt's embedded dependency receipt, and that dependency receipt's embedded source receipt byte-for-byte to the supplied source receipt;
- the continuity receipt's queue anchor SHA-256 to that same queue receipt.

A digest swap, actor swap, or issue swap is an input error rather than a HOLD.

## Dispositions

- **`APPLY_ELIGIBLE`** — queue says apply, continuity is `DISCOVERED`, and
  source is `SOURCE_ALIGNED`.
- **`REPLAN_BEFORE_APPLY`** — queue says apply, continuity is `DISCOVERED`,
  but source is `SOURCE_DRIFT_REPLAN`.
- **`WAIT_ASSIGNMENT`** — durable continuity says `APPLIED`; do not duplicate
  the provider application.
- **`WAIT_DEPENDENCIES`** — durable continuity says `ASSIGNED`, but a prerequisite issue remains open or a closure-clear prerequisite still lacks exact default-branch landing evidence.
- **`IMPLEMENT_ASSIGNED_SCOPE`** — the only implementation activation. It
  requires queue=`IMPLEMENTATION_ELIGIBLE`, continuity=`ASSIGNED`,
  continuity disposition not held, same actor/issue/queue/source anchors,
  source=`SOURCE_ALIGNED`, and fulfillment=`DEPENDENCIES_FULFILLED`.
- **`HOLD_SOURCE`** — source is `HOLD`, or an assigned scope has source drift.
- **`HOLD_DEPENDENCIES`** — fulfillment evidence is stale, contradictory, or otherwise held and must be refreshed or reconciled.
- **`HOLD_RECONCILE`** — contradictory lifecycle/provider receipts or a durable
  continuity reconciliation hold.

Lifecycle states `SUBMITTED`, `APPROVED`, `PAYMENT_SENT`, `PAID`, and
`REJECTED` can never produce implementation activation.

## Verifiable evidence envelope

An activation receipt retains the complete native queue, source-readiness, dependency-fulfillment, and continuity receipts under `evidence`. Verification does not trust the outer
activation SHA-256 by itself. `verify_activation_receipt()`:

1. requires the exact activation receipt and evidence field sets;
2. re-runs all four native receipt verifiers;
3. re-checks issue, actor, and queue-anchor equality through
   `compile_activation()`;
4. recomputes the activation state machine from the retained native evidence; and
5. requires exact equality with the supplied activation receipt, including its
   digest.

Therefore, changing a non-implementation receipt to
`IMPLEMENT_ASSIGNED_SCOPE` and recomputing only
`activation_receipt_sha256` still fails verification. Extra semantic fields
also fail closed.

Receipts created by the earlier self-hash-only implementation do not contain the
native evidence envelope. They intentionally fail the repaired verifier and
must be regenerated from the original native receipts.

## CLI

```bash
python -m concierge.grantfox_activation_gate activation.json --json
```

Exit code 2 means a `HOLD_*` disposition. Other internally consistent advisory
states return 0.

## Authority ceiling

Every output fixes all mutation authorities to false:

```json
{
  "advisory_only": true,
  "provider_application_authority": false,
  "implementation_write_authority": false,
  "submission_authority": false,
  "adjudication_authority": false,
  "payment_or_wallet_authority": false
}
```

The activation receipt is canonical-JSON SHA-256 bound, but verification also
replays the native verifiers and activation state machine from the retained
evidence. The digest remains tamper evidence only; it is not provider authority
and it does not turn a possible/discretionary reward into an award or payment.


## Dependency / fulfillment rule

Activation never infers prerequisite clearance from absence. Every activation request carries a verified dependency-fulfillment receipt, which itself embeds and verifies the closure-level dependency-readiness receipt.

Issues with no prerequisites use explicit `dependencies: []` and `landings: []`; readiness compiles to `DEPENDENCIES_CLEAR` and fulfillment compiles to `DEPENDENCIES_FULFILLED`. For issues with prerequisites, issue closure alone is never enough: assigned implementation requires fresh evidence that each prerequisite capability landed in the repository's default-branch ancestry.

Open prerequisites remain a normal `WAIT_DEPENDENCIES` state. Closed prerequisites with missing landing proof also wait. Stale or contradictory fulfillment evidence becomes `HOLD_DEPENDENCIES`. Pre-assignment application routing is not blocked merely because dependency fulfillment is not yet ready; the stronger fulfillment gate becomes mandatory at `ASSIGNED` before `IMPLEMENT_ASSIGNED_SCOPE`.
