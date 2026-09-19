# Ajo #912 — poison-event / dead-letter source audit

**Seat:** ZZ-Sol-Variegate / GPT-5.6 Sol  
**Date:** 2026-09-19  
**Upstream:** `Ajo-contrib/soroban-ajo`  
**Issue:** #912 — *Dead-letter / poison-event handling*  
**Pinned upstream master:** `1b87ea7344ae3d871e54abff05eabe5113bd2956`  
**Provider state at audit:** GrantFox says **Unassigned** and exposes **Apply to this issue**. GitHub issue is OPEN, assignees=null, one generic assignment-request comment, and no PR carrier returned by exact `912` PR search.

This is a **pre-assignment source audit**, not an upstream implementation or reward claim.

## Executive finding

Issue #912 is valid, but there are **two different replay surfaces** and they should not be conflated.

1. `backend/src/events/` is the literal off-chain event-sourcing/replay module. Its own README/ADR says it is currently **unused by production callers**. `rebuildProjection()` reduces the full/snapshot-tail event array with no per-event error isolation. A malformed persisted event or projection exception aborts the whole rebuild. There is no poison-event state, quarantine/dead-letter store, retry count, skip decision, operator API, or runbook.
2. The production-relevant Soroban ingestion path is `backend/src/services/blockchainListener.ts`. It already has transactional dedup/checkpointing and backfill, but a poison event exposes a more serious **checkpoint-ordering** failure under the live stream because live events are launched concurrently and checkpoint writes are unconditional.

The safest assignment implementation should explicitly cover the active blockchain listener, and only also harden the unused `backend/src/events/` module if the maintainer confirms that #912 intends both surfaces.

## Source pins

| Surface | Path | Blob SHA |
|---|---|---|
| Blockchain listener | `backend/src/services/blockchainListener.ts` | `a7031bc65307f7c912a753ed13940718346562a3` |
| Event processor | `backend/src/services/eventProcessor.ts` | `d06c2ff4f8a990ec3590b6f9c20302730d2f8ab8` |
| Listener tests | `backend/tests/unit/blockchainListener.test.ts` | `48d65ccfd63a4260eeef15ab2ddede8f31aab1f4` |
| Prisma schema | `backend/prisma/schema.prisma` | `a8da55bbd0adaf04f767cd1ab923b186a1e2f84d` |
| Event store | `backend/src/events/eventStore.ts` | `87551db7cc2c2070b3481731b8892f3bbd0c91f5` |
| Projection replay | `backend/src/events/projections/index.ts` | `2c21f829685dd7491ac814e4d4507cd3676b08e0` |
| Event module README | `backend/src/events/README.md` | `f3eae4a556a7fc1e4430340ec916115e01c72a11` |
| Replay correctness tests | `backend/src/__tests__/unit/events/replayCorrectness.test.ts` | `7b294a7b46db78a4d9a3f5ea428b17ccad7da2fc` |
| Snapshot replay tests | `backend/src/__tests__/unit/events/snapshotReplay.test.ts` | `98970c8af54c47f9444c1992845292971df60b23` |
| Fraud runbook pattern | `backend/docs/FRAUD_RUNBOOK.md` | `66050549739c959481d7a119ebcc3b1ab635cd0b` |
| Backend package scripts | `backend/package.json` | `817d678882942193301248e5dac6b6fddabb82e8` |

## Finding A — literal replay aborts on the first poison event

`rebuildProjection()` currently does:

```ts
const events = await eventStore.getByAggregateId(aggregateId, fromVersion)
const state = events.reduce(projection.apply.bind(projection), initialState)
```

There is no event-level `try/catch`. If `projection.apply()` throws on event N:

- events N+1…end are never applied;
- no structured poison-event record is persisted;
- no retry/attempt count or operator disposition exists;
- no alert is guaranteed;
- no “halt vs quarantine-and-continue” policy is recorded;
- no runbook tells an operator how to identify, inspect, repair, retry, or explicitly skip the event.

The existing replay tests verify deterministic results for **well-formed** histories only. They do not inject a corrupt record between two good records.

### Scope caveat

The current `backend/src/events/README.md` explicitly says this event-sourcing module is unused and has zero production call sites outside the module. Therefore hardening only this reducer would satisfy the issue text narrowly while leaving the production chain-ingestion risk below untouched.

## Finding B — live Soroban events are processed concurrently

`startLiveStream()` calls:

```ts
onmessage: (event) => {
  this.reconnectDelay = RECONNECT_DELAY_MS
  this.handleRawEvent(event)
}
```

and `handleRawEvent()` calls `this.processEvent(...).catch(...)` without awaiting or serializing it.

Therefore events A, B, C can be in flight at once even though checkpoint state is a single ordered cursor.

## Finding C — checkpoint writes are not monotonic

Inside each event transaction, `advanceCheckpoint(tx, ledger, pagingToken)` unconditionally upserts:

```ts
update: { lastLedger: ledger, lastPagingToken: pagingToken }
```

There is no compare-and-set / monotonic fence against the current checkpoint.

### Concrete out-of-order success failure

1. Event A = ledger 100, slow handler.
2. Event B = ledger 101, fast handler.
3. B commits first → DB checkpoint = 101/B; in-memory checkpoint = 101/B.
4. A commits later → DB checkpoint **regresses to 100/A**.
5. A does not update the in-memory value because `100 > 101` is false.

The process now has split-brain checkpoint state: memory says 101/B, durable DB says 100/A.

A restart can replay work unnecessarily; more importantly, a poison/error interleaving can create the opposite problem and move the durable cursor beyond unresolved work.

## Finding D — good → bad → good can leap over poison

Concrete sequence:

1. A at ledger 100 begins and succeeds slowly.
2. B at ledger 101 is poison and rolls back.
3. C at ledger 102 succeeds and commits checkpoint 102/C.
4. B is unresolved, but durable checkpoint may now be 102.
5. Restarted backfill starts at `lastProcessedLedger + 1 = 103`.

B can be skipped permanently by ledger-based backfill even though its transaction never committed.

This is the exact integrity failure #912 is trying to prevent: derived state can silently omit a record while later state continues.

## Finding E — same-ledger poison exposes a second resume gap

Backfill processes fetched events sequentially, which is safer than live mode, but it checkpoints each event and restart range selection is ledger-based:

```ts
const fromLedger = this.lastProcessedLedger + 1
```

If ledger 500 contains good event A followed by poison event B:

1. A succeeds → checkpoint ledger 500, paging token A.
2. B throws → the whole backfill loop exits to the outer catch.
3. On restart, backfill begins at ledger 501.

Unless the live SSE recovery from paging token A happens before restart and successfully resolves B, the backfill path itself can never revisit B because it discards the paging-token position inside the checkpointed ledger.

The code persists `lastPagingToken`, but backfill does not use it to resume within the checkpoint ledger.

## Finding F — duplicate recovery does not advance a stale checkpoint

Inside `processEvent()`, a `P2002` dedup hit returns from the transaction callback before `advanceCheckpoint()`.

If an event was committed but the durable checkpoint is stale/regressed due to another race, replaying that already-processed event does not repair the checkpoint. The stream can continue, but the durable resume point can remain behind and cause repeat churn.

## Finding G — event identity can collapse multiple same-type events in one transaction

The uniqueness fence is:

`@@unique([contractId, txHash, eventType])`

Soroban transactions can contain multiple emitted events. If one transaction emits two events of the same parsed `eventType`, the second shares `contractId + txHash + eventType` and is treated as a duplicate even when it is a distinct event.

The durable identity should include the canonical event id / paging token / event index, not only transaction hash and type.

This is adjacent to #912 because a poisoned or retried multi-event transaction must not cause unrelated sibling events to disappear under dedup.

## Existing defenses worth preserving

- Per-event state changes, dedup insertion and checkpoint update are already in one Prisma transaction.
- Backfill processes its fetched array sequentially.
- Listener coordination uses a distributed lock.
- Processing errors are counted and logged.
- Backfill failure fires a warning.
- Listener lag has warning/critical alert thresholds.
- Existing listener tests cover basic dedup, helper checkpoint writes and happy-path crash recovery.

The fix should extend these invariants rather than replace them.

## Recommended bounded implementation contract

### 1. Define canonical event identity

Persist an identity that uniquely names one emitted event, preferably the Soroban/Horizon event id or paging token plus contract/network. Keep txHash/eventType as searchable metadata, not the sole uniqueness key.

### 2. Serialize checkpoint-bearing processing

Do not let independent live promises update one ordered checkpoint.

Acceptable patterns:
- one FIFO processing chain/queue per listener lease; or
- a bounded worker pool plus a commit barrier that advances only through the highest **contiguous resolved** event.

For this service, a single ordered processing chain is the smallest auditable change.

### 3. Make checkpoint advancement monotonic and position-aware

Checkpoint should represent an exact stream position, not only a ledger number.

Required invariant:

> No successful event may advance the durable checkpoint past an earlier unresolved event.

Use paging token/event sequence as the authoritative ordering position; ledger is useful metadata but insufficient when multiple events share a ledger.

### 4. Persist poison-event state

Add an explicit durable model, e.g. `PoisonBlockchainEvent` / `DeadLetterEvent`, with at least:

- canonical event id / paging token;
- contract/network;
- ledger;
- tx hash;
- parsed type if available;
- raw payload or a safe serialized/hash representation;
- status: `PENDING | RETRYING | QUARANTINED | RESOLVED | SKIPPED`;
- attempt count;
- first/last failure timestamp;
- error class/message/stack or bounded diagnostic;
- operator resolution metadata.

Never store secrets if raw upstream payloads can contain them.

### 5. Choose and document one failure policy

The issue allows either skip-with-alert or halt-with-clear-diagnostics. For financial derived state, default should be **halt checkpoint advancement at the poison position** while preserving later source events for deterministic replay.

A manual operator may then:
- retry after code/data repair;
- mark resolved after successful replay; or
- explicitly skip/quarantine with reason, audit identity and alert.

A skip must be an explicit durable decision, never an automatic log-and-continue side effect.

### 6. Add an operator runbook

Pattern it after `backend/docs/FRAUD_RUNBOOK.md`.

Minimum sections:
- detection signals / metrics / alerts;
- how to query poison records;
- how to identify the source event and checkpoint;
- retry procedure;
- data/code repair procedure;
- explicit skip/quarantine procedure with required reason;
- verification that checkpoint resumes and lag returns to normal;
- rollback/escalation;
- “do not delete the poison row to make the alert disappear.”

### 7. Keep the unused event-sourcing module honest

If #912 also covers `backend/src/events/projections/index.ts`, add a replay result/policy rather than a bare `Array.reduce`:
- event-by-event application;
- structured failure containing event id/sequence/type;
- configurable `halt` or explicit operator-approved `skip` behavior;
- tests proving good→bad→good semantics.

Do not imply this module protects production group state while ADR-011 says it is unused.

## Hostile test matrix

### Blockchain listener

1. **good → poison → good, increasing ledgers**
   - later good event may be fetched/buffered;
   - checkpoint MUST remain before poison until it is resolved/skipped;
   - after retry/resolution, checkpoint advances contiguously and later good event is applied once.

2. **good → poison → good, same ledger**
   - restart must resume at exact paging/event position inside that ledger;
   - poison and following sibling event must not disappear due to `lastLedger + 1`.

3. **slow earlier success + fast later success**
   - checkpoint never regresses.

4. **slow poison + fast later success**
   - later completion cannot leap checkpoint beyond poison.

5. **duplicate immediately after stale checkpoint**
   - dedup hit repairs/advances contiguous checkpoint as appropriate, without re-running side effects.

6. **two same-type events in one tx**
   - both distinct event ids are applied exactly once.

7. **process crash after poison persisted but before operator action**
   - restart surfaces same unresolved poison and does not silently move past it.

8. **explicit skip**
   - requires durable reason/actor/timestamp;
   - emits alert/audit event;
   - checkpoint advances only after skip disposition commits.

### Off-chain `backend/src/events/` replay

9. **valid → malformed payload → valid**
   - halt mode reports exact bad event and does not return a partial projection as success.

10. **projection throw after snapshot**
    - diagnostic identifies aggregate, snapshot version, event version and sequence number.

11. **explicit quarantine/skip policy**
    - result marks degraded/incomplete state; never silently returns it as a normal full projection.

## Target test commands

From `backend/`:

```bash
npm run type-check
npm test -- --runInBand tests/unit/blockchainListener.test.ts
npm test -- --runInBand src/__tests__/unit/events/replayCorrectness.test.ts
npm test -- --runInBand src/__tests__/unit/events/snapshotReplay.test.ts
```

If the implementation adds a dedicated poison-event suite, include it explicitly in the merge receipt.

## Assignment-safe next step

Provider state is **Unassigned / Apply**. Do not start the upstream code change until the GrantFox/maintainer assignment gate is actually satisfied. Once assigned, implementation should start from the pinned files above, refresh `master`, re-audit for intervening listener changes, and keep the checkpoint-contiguity invariant as the acceptance fence.
