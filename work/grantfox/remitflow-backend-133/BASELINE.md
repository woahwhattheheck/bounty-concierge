# GrantFox pre-assignment baseline — RemitFlow/RemitFlow-Backend #133

Lane: `GFOX3-20260919-remitflow-remitflow-backend-133`  
Issue: https://github.com/RemitFlow/RemitFlow-Backend/issues/133  
GrantFox: https://contribute.grantfox.xyz/org/RemitFlow/repo/RemitFlow-Backend/issue/133

## Authority fence

This packet is **pre-assignment evidence only**. It does not establish a GrantFox
assignment, reward, payout, or upstream implementation authority.

Observed on 2026-09-19:

- GrantFox rendered **Unassigned**, **Apply to this issue**, one application per
  user, and **0 comments**.
- GitHub issue #133 also returned zero comments.
- No linked pull request was surfaced by the GrantFox page.
- Upstream repository permissions for `woahwhattheheck` are
  `pull=true / push=false`.
- A provider-browser publication attempt from this seat was blocked before the
  browser run executed. Therefore **no application/comment was published by
  this seat** and there is no assignment receipt to rely on.

Any implementation must begin from a fresh provider/maintainer state check.

## Pinned upstream source

Default branch: `main`  
Pinned commit: `b0a004ab8aa00b1e6f262aa84ea2b0ec0a622e1b`

Exact evidence blobs:

- `src/services/transferService.js` — `1517f6254bf18cf1be4e7d52cf369e637d8ffb0f`
- `src/config/constants.js` — `fccc9dbddafc02aac8d03b059b31929e9935895c`
- `src/store/index.js` — `0024fac7dfbefe34054b9ad0f3ce4a8164828ee7`
- `src/services/stellarService.js` — `e77cea9f8ff55a38de68ee55f4a5563a7c748321`
- `src/services/idempotencyService.js` — `40b50a6f68d2ffc2da2ee0db733001be3fa47312`
- `src/controllers/transferController.js` — `4fb2e7273898d09b671d09758ac2f90d9e249daf`
- `src/services/auditService.js` — `4e5140352354a4279fe54c4fee8721981ec82233`
- `src/utils/orderedIndex.js` — `3acdfc7cfa28de6e62ab8957cc89a4e2c9531719`
- `test/transferIdempotency.test.js` — `209051a011b8ff46891415eec5a885797a6f797c`

Relevant landed predecessor:

- `ee6880defdbf7eba19b48a75abfa4ddb45ac607f` — transfer creation idempotency
  change (PR #135, commit message closes issue #128).

## Current-source correction: the literal issue lifecycle does not exist

Issue #133 describes races among **cancel, retry, approve, and settle** operations
and calls out settlement workers. Current `main` does not expose that model.

`src/config/constants.js` defines only:

```text
pending -> claimed
pending -> cancelled
claimed -> []
cancelled -> []
```

There are no approve, retry, or settle states/entrypoints in the pinned source,
and there is no settlement-worker path to harden as written in the issue.

Current mutation paths are:

| Path | Current behavior | Provider / external side effect |
| --- | --- | --- |
| create | quote → `submitPayment` → build transfer → store/index → audit → idempotency complete | mock Stellar payment occurs **before** local persistence |
| claim | validate transition → mutate status → generate claimable-balance id → audit | local mock id generation only |
| cancel | validate transition → mutate status → audit | none |
| archive/unarchive | mutate archive timestamps | none |

That makes a literal implementation of "approve/retry/settle worker" behavior a
new product/state-machine design, not a focused concurrency fix. The maintainer
should confirm whether #133 expects new lifecycle states before any contributor
invents them.

## Concrete remaining concurrency hazards on current main

### 1. Terminal-state mutation is check-then-mutate with no version fence

`claimTransfer` and `cancelTransfer` fetch the same mutable object and call
`transition()`, which checks the current status then mutates it in place. The
demo is synchronous today, but the service contract has no version or
compare-and-swap boundary. If storage becomes asynchronous or worker/callback
paths are added, stale commands have no explicit expected-version contract.

A focused fix should centralize state mutation around an immutable command
precondition such as `expectedVersion` (or equivalent store revision), with a
typed conflict for stale attempts and exactly one audit event for the winner.

### 2. Provider success followed by local failure can reopen the idempotency key

Creation idempotency reserves the actor/key **before** `submitPayment`, which
correctly blocks a concurrent duplicate while the provider call is in flight.
However, `createTransfer()` wraps the entire unchecked operation in one catch
and calls `idempotencyService.release(...)` for *any* later exception.

The unchecked order is:

1. compute quote;
2. call `stellarService.submitPayment`;
3. build transfer object;
4. `store.transfers.set`;
5. `store.transferIndex.append`;
6. `auditService.addEntry`;
7. `idempotencyService.complete`.

Therefore an exception after step 2 — for example index/audit/persistence
failure — releases the reservation even though the provider may already have
succeeded. A retry can then call the provider again. This is the sharpest
current-source path matching #133's "provider retries cannot settle twice"
acceptance criterion.

The existing idempotency tests prove provider-*throw* recovery, but do not prove
provider-success/local-failure recovery.

### 3. Restart semantics are process-local, not durable

Transfers, ordered indexes, audit history, and idempotency records all live in
memory and reset together. Existing tests intentionally document that property.

That means #133's required "worker-restart" validation cannot honestly prove
durable post-provider recovery on current architecture. Either:

- the accepted scope is process-local concurrency only, with restart explicitly
  out of durability scope; or
- the maintainer authorizes a persistence/operation-journal seam large enough to
  survive process restart.

Do not claim restart-safe exactly-once settlement while all operation state is
still process memory.

### 4. Transition mutation and audit append are not atomic

`claimTransfer` and `cancelTransfer` mutate the transfer before appending the
audit record. If audit append fails, the state change remains with no matching
audit outcome. A versioned transition boundary should define whether state and
audit commit atomically, or record a recoverable discrepancy explicitly.

## Post-assignment implementation contract

A current-source-compatible implementation should begin by getting explicit
maintainer confirmation on the approve/retry/settle drift. Without adding
unrequested lifecycle surface, the focused contract is:

1. add a monotonic transfer revision/version;
2. centralize terminal status changes through a compare-and-swap transition
   primitive that takes the observed/expected version;
3. make stale commands return a typed conflict and emit no loser audit event;
4. keep exactly one terminal outcome when claim/cancel interleave;
5. split provider outcome from generic local failure so a provider-success /
   local-failure path cannot release the operation and submit payment twice;
6. define a durable terminal/provider receipt boundary before claiming
   restart-safe semantics;
7. make mutation + audit outcome atomic where the repository's storage model can
   support it, or record an explicit recoverable discrepancy rather than
   silently leaving unaudited state;
8. reuse the existing actor-scoped idempotency and audit primitives rather than
   creating a second parallel mechanism.

If maintainers confirm that #133 intentionally requires new approve/retry/settle
states or a settlement worker, update this packet with that authoritative state
model first, then implement against it.

## Hostile regression matrix

Minimum proof after assignment:

| Case | Expected |
| --- | --- |
| claim and cancel race from same pending revision | exactly one terminal winner; loser gets typed conflict |
| stale command with old revision | no mutation, no audit event, deterministic conflict |
| duplicate same command/retry | no second terminal mutation or duplicate audit |
| provider throws before success | reservation can be retried safely |
| provider succeeds, local persistence/index/audit then fails | retry must not submit provider payment a second time |
| audit append fails after attempted terminal mutation | no silent unaudited committed state |
| process restart with process-local store | test documents loss honestly; does not claim durable recovery |
| durable journal/store added with maintainer approval | provider terminal result survives restart and replays without re-submission |
| unrelated create idempotency regression suite | stays green |
| existing lifecycle/API suite | stays green without weakened assertions |

## Source-specific provider application text

An authenticated seat can reuse this after one fresh source/provider check:

> I reviewed current RemitFlow-Backend main at
> `b0a004ab8aa00b1e6f262aa84ea2b0ec0a622e1b` before applying. The live code
> has a narrower lifecycle than the issue text: `TRANSFER_STATUS` is
> pending/claimed/cancelled, with pending → claimed|cancelled only; there are no
> approve/retry/settle endpoints or settlement worker today. The provider side
> effect currently occurs synchronously during `createTransfer` before
> persistence, while the landed creation-idempotency change already reserves an
> actor-scoped key before the provider call. I found one remaining double-submit
> window: any exception after provider success but before local completion is
> caught by the same release path, which can reopen the key and let a retry call
> the provider again. I would first preserve that current-source reality, then
> implement a focused concurrency contract around the actual mutation boundary:
> versioned/CAS terminal transitions so one winner commits, typed stale-command
> conflicts, replay-safe provider terminal handling, and hostile tests for
> interleavings, duplicate commands/callbacks, provider-success/local-failure,
> audit failure, and restart semantics. If #133 intentionally expects introducing
> approve/settle worker states that are not on current main, I will confirm that
> with the maintainer before inventing API surface. I will keep the PR focused
> and reuse the existing idempotency/audit primitives rather than duplicate them.

## Reuse guidance for the swarm

Do not restart from the issue prose. Re-pin `main`, refresh provider
assignment, and check for a newly linked PR. The current high-value residual is
the provider-success/local-failure replay window plus a versioned terminal
transition boundary — unless the maintainer explicitly supplies a larger worker
state machine.
