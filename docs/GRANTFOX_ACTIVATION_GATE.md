# GrantFox activation gate

`concierge.grantfox_activation_gate` composes the four independent GrantFox
control receipts that workers otherwise have to interpret separately:

1. `grantfox-queue-gate/v1` — current provider queue/application state.
2. the verified GrantFox source-readiness receipt — pinned repository/source state.
3. `grantfox-dependency-readiness-receipt/v1` — explicit prerequisite issue readiness, including an explicit zero-dependency receipt.
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
  "dependency_receipt": {"...": "verified GrantFox dependency-readiness receipt"},
  "continuity_receipt": {"...": "verified grantfox-application-continuity/v1 receipt"}
}
```

All four receipts must verify using their native verifier. The gate then binds:

- case-insensitive owner/repository + exact issue number;
- exact actor between queue and continuity;
- the source receipt's embedded queue receipt byte-for-byte to the supplied queue
  receipt;
- the dependency receipt's embedded source receipt byte-for-byte to the supplied
  source receipt;
- the continuity receipt's queue anchor SHA-256 to that same queue receipt.

A digest swap, actor swap, or issue swap is an input error rather than a HOLD.

## Dispositions

- **`APPLY_ELIGIBLE`** — queue says apply, continuity is `DISCOVERED`, and
  source is `SOURCE_ALIGNED`.
- **`REPLAN_BEFORE_APPLY`** — queue says apply, continuity is `DISCOVERED`,
  but source is `SOURCE_DRIFT_REPLAN`.
- **`WAIT_ASSIGNMENT`** — durable continuity says `APPLIED`; do not duplicate
  the provider application.
- **`WAIT_DEPENDENCIES`** — durable continuity says `ASSIGNED`, but one or more
  verified prerequisite issues remain open.
- **`IMPLEMENT_ASSIGNED_SCOPE`** — the only implementation activation. It
  requires queue=`IMPLEMENTATION_ELIGIBLE`, continuity=`ASSIGNED`,
  continuity disposition not held, same actor/issue/queue/source anchors,
  source=`SOURCE_ALIGNED`, and dependency=`DEPENDENCIES_CLEAR`.
- **`HOLD_SOURCE`** — source is `HOLD`, or an assigned scope has source drift.
- **`HOLD_DEPENDENCIES`** — dependency evidence is itself stale or held and
  must be refreshed or reconciled.
- **`HOLD_RECONCILE`** — contradictory lifecycle/provider receipts or a durable
  continuity reconciliation hold.

Lifecycle states `SUBMITTED`, `APPROVED`, `PAYMENT_SENT`, `PAID`, and
`REJECTED` can never produce implementation activation.

## Verifiable evidence envelope

An activation receipt retains the complete native queue, source-readiness,
dependency-readiness, and continuity receipts under `evidence`. Verification
does not trust the outer activation SHA-256 by itself.
`verify_activation_receipt()`:

1. requires the exact activation receipt and evidence field sets;
2. re-runs all four native receipt verifiers;
3. re-checks issue, actor, queue, source, and dependency anchor equality through
   `compile_activation()`;
4. recomputes the activation state machine from the retained native evidence; and
5. requires exact equality with the supplied activation receipt, including its
   digest.

The queue and continuity trust roots are reconstructive as well. The queue
receipt retains the producer observation from which its disposition and provider
snapshot are derived. The continuity receipt retains the verified queue receipt,
configured actor, and producer event sequence. Their native verifiers replay
their compilers and require exact receipt equality instead of accepting a
self-hash.

Therefore, changing a non-implementation activation receipt, queue disposition,
provider-snapshot summary, or continuity lifecycle summary to implementation
state and recomputing the affected SHA-256 values still fails when the retained
producer evidence says otherwise. The hostile integration test performs exactly
that native queue + continuity rehash promotion and requires rejection.

Receipts created by the earlier self-hash-only queue, continuity, or activation
formats intentionally fail the repaired verifiers and must be regenerated from
their original producer evidence.

These are semantic and integrity checks over retained advisory evidence, not
digital signatures from GrantFox or GitHub. They do not independently
authenticate an external assignment, award, adjudication, or payment.

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


## Dependency rule

Activation never infers prerequisite clearance from absence. Every activation
request carries a verified dependency-readiness receipt. Issues with no
prerequisites use an explicit empty dependency list, which compiles to
`DEPENDENCIES_CLEAR`. Assigned issues with verified open prerequisites compile
to `WAIT_DEPENDENCIES` and cannot produce `IMPLEMENT_ASSIGNED_SCOPE`.
