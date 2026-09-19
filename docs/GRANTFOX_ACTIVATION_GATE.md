# GrantFox activation gate

`concierge.grantfox_activation_gate` composes the three independent GrantFox
control receipts that workers otherwise have to interpret separately:

1. `grantfox-queue-gate/v1` — current provider queue/application state.
2. the verified GrantFox source-readiness receipt — pinned repository/source state.
3. `grantfox-application-continuity/v1` — monotonic durable lifecycle evidence.

The gate is intentionally advisory-only. It performs no provider application,
assignment, GitHub write, submission, adjudication, sponsor contact, payment, or
wallet action.

## Why a compositor is necessary

Each underlying receipt is useful on its own, but no one receipt proves that all
three views agree. In a fast swarm, these are materially different facts:

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
  "continuity_receipt": {"...": "verified grantfox-application-continuity/v1 receipt"}
}
```

All three receipts must verify using their native verifier. The gate then binds:

- case-insensitive owner/repository + exact issue number;
- exact actor between queue and continuity;
- the source receipt's embedded queue receipt byte-for-byte to the supplied queue
  receipt;
- the continuity receipt's queue anchor SHA-256 to that same queue receipt.

A digest swap, actor swap, or issue swap is an input error rather than a HOLD.

## Dispositions

- **`APPLY_ELIGIBLE`** — queue says apply, continuity is `DISCOVERED`, and
  source is `SOURCE_ALIGNED`.
- **`REPLAN_BEFORE_APPLY`** — queue says apply, continuity is `DISCOVERED`,
  but source is `SOURCE_DRIFT_REPLAN`.
- **`WAIT_ASSIGNMENT`** — durable continuity says `APPLIED`; do not duplicate
  the provider application.
- **`IMPLEMENT_ASSIGNED_SCOPE`** — the only implementation activation. It
  requires queue=`IMPLEMENTATION_ELIGIBLE`, continuity=`ASSIGNED`,
  continuity disposition not held, same actor/issue/queue anchor, and
  source=`SOURCE_ALIGNED`.
- **`HOLD_SOURCE`** — source is `HOLD`, or an assigned scope has source drift.
- **`HOLD_RECONCILE`** — contradictory lifecycle/provider receipts or a durable
  continuity reconciliation hold.

Lifecycle states `SUBMITTED`, `APPROVED`, `PAYMENT_SENT`, `PAID`, and
`REJECTED` can never produce implementation activation.

## Receipt verification

The activation receipt retains the complete verified queue, source-readiness, and
continuity receipts under `evidence`. Verification is semantic, not merely a
self-hash check:

1. the activation receipt's canonical SHA-256 must match its bytes;
2. the queue receipt is reconstructed through the real queue compiler and must
   exactly match the retained queue evidence;
3. source readiness is reverified through its native semantic verifier;
4. continuity is reconstructed from the retained queue + event ledger through
   the real continuity compiler and must exactly match the retained continuity
   evidence;
5. the activation decision is recompiled from those three receipts and the full
   expected receipt must equal the supplied receipt.

Therefore a caller cannot turn an `APPLY_ELIGIBLE` or `WAIT_ASSIGNMENT`
receipt into `IMPLEMENT_ASSIGNED_SCOPE` by editing derived fields and
recomputing SHA-256. A rehashed forged queue/continuity state also fails the
semantic producer-to-consumer replay.

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

The activation receipt is canonical-JSON SHA-256 bound **and** semantically
recompiled from its retained native evidence during verification. The digest is
tamper evidence, not authority; even a correctly rehashed forged decision is
rejected when it disagrees with the authenticated queue/source/continuity
semantics. Nothing in the receipt turns a possible/discretionary reward into an
award or payment.
