# Ajo-contrib/soroban-ajo #912 — poison-event / checkpoint readiness baseline

Captured 2026-09-19 by ZZ-SolForge / GPT-5.6 Sol.

## Assignment fence

- GitHub issue #912 is OPEN and unassigned; labels include `GrantFox OSS`, `Maybe Rewarded`, `Third Campaign`, `backend`, and `reliability`.
- GrantFox public page resolves, shows **Unassigned**, and exposes **Apply to this issue**.
- The connected upstream repository permission is read-only. This packet is source/readiness evidence only; no upstream implementation begins without official provider or maintainer assignment.
- No current Slack TAKE/DONE/carrier for #912 was found at claim time, and GitHub search returned no PR carrier.
- No fixed reward amount is verified.

## Pinned source generation

`Ajo-contrib/soroban-ajo@1b87ea7344ae3d871e54abff05eabe5113bd2956` (`master`)

| Path | Git blob | Relevance |
|---|---|---|
| `backend/src/services/blockchainListener.ts` | `a7031bc65307f7c912a753ed13940718346562a3` | Backfill, live SSE processing, per-event transaction, singleton checkpoint. |
| `backend/src/services/eventProcessor.ts` | `d06c2ff4f8a990ec3590b6f9c20302730d2f8ab8` | Event parsing and transactional domain dispatch. |
| `backend/tests/unit/blockchainListener.test.ts` | `48d65ccfd63a4260eeef15ab2ddede8f31aab1f4` | Existing idempotency/restart/checkpoint/lag coverage. |
| `backend/prisma/schema.prisma` | `a8da55bbd0adaf04f767cd1ab923b186a1e2f84d` | Singleton `ListenerCheckpoint` and `ProcessedBlockchainEvent`; no dead-letter/quarantine model. |

Repository search found no current dead-letter / poison-event / quarantine implementation.

## Concrete residual failure modes

### 1. Backfill is isolated transactionally per event, but not operationally per event

`backfill()` fetches an ordered batch and executes:

```ts
for (const event of events) {
  await this.processEvent(event)
}
```

The enclosing `try/catch` covers the whole backfill. If one event's parse or handler throws, the loop aborts. The catch logs/alerts that backfill failed and `start()` then proceeds to `startLiveStream()`.

That is clearer than an uncaught process crash, but it still leaves every later backfill event in the fetched batch unprocessed and has no durable poison-event identity, retry count, quarantine state, or operator resolution record.

### 2. Live SSE processing is not serialized with checkpoint order

`handleRawEvent()` calls:

```ts
this.processEvent(sorobanEvent).catch(...)
```

without awaiting a single ordered consumer queue. Each successful `processEvent()` transaction writes the shared singleton checkpoint to **that event's ledger/paging token**, then updates the in-memory checkpoint if the ledger is greater than the cached value.

Therefore two live events can be in flight concurrently. If earlier event A fails but later event B succeeds, B can commit the singleton checkpoint past A. A's transaction rolls back and A is not marked processed, yet a restart resumes from the later checkpoint. That defeats the source comment claiming failed events “will be retried on the next start” unless the upstream stream happens to redeliver them independently.

This is a stronger integrity risk than merely “one poison event stops replay”: a poison event can be skipped by later successful checkpoint advancement.

### 3. No durable operator workflow

Current schema has:
- one `ListenerCheckpoint` row;
- one exactly-once `ProcessedBlockchainEvent` table.

It has no poison/dead-letter table with event identity, raw payload/reference, failure class, attempt count, first/last failure times, disposition, or resolution/replay metadata. Repository search found no poison-event runbook comparable to `backend/docs/FRAUD_RUNBOOK.md`.

## Recommended assigned implementation

After official assignment:

1. **Serialize ordered checkpoint advancement.**
   - Feed both backfill and live events through one ordered consumer or equivalent monotonic checkpoint coordinator.
   - A later event must never advance durable checkpoint past an unresolved earlier event unless the system explicitly records a supported skip/quarantine disposition.

2. **Make the failure policy explicit and durable.**
   - Preferred safe default: halt ordered progression at a poison event, persist a dead-letter record, alert loudly, and keep reconnect/restart anchored before it.
   - If skip-with-alert is supported, require an explicit durable operator disposition before checkpoint progression; never make “log and continue” silently equivalent to success.

3. **Add a dead-letter model.**
   Bind at minimum contract/event identity, ledger, paging token, tx hash, parseable type when available, failure class/message, attempts, timestamps, raw/source reference, status, and operator resolution metadata. Avoid storing secrets.

4. **Bound retries.**
   Distinguish transient infrastructure errors from deterministic parse/schema poison. Prevent an infinite hot retry loop while preserving recoverability.

5. **Add runbook documentation.**
   Cover detection/alerts, inspecting the failed record, confirming surrounding checkpoint state, replaying after code/data repair, explicit quarantine/skip approval if supported, and proving no later event was silently skipped.

6. **Regression tests.**
   - backfill [good A, poison B, good C]: C must not become processed while B unresolved under halt policy;
   - live concurrent A/B: later success must not move checkpoint past earlier failed ledger;
   - restart after poison: resume before/at poison rather than after it;
   - repaired poison replay succeeds once and checkpoint advances;
   - duplicate redelivery remains idempotent;
   - operator quarantine/skip path, if implemented, is explicit/audited.

## Acceptance evidence

A final assigned PR should show exact checkpoint/dead-letter schema changes, focused poison/restart tests, alert/runbook changes, and a clean proof that durable checkpoint never outruns unresolved ordered events.

No provider application, source mutation, submission, wallet/payment, or reward authority is created by this packet.
