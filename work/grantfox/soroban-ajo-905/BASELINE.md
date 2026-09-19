# Ajo #905 — saga recovery deadline / lease baseline

Lane: `ZZLK-GFOX-002/R-stale-saga-recovery-contract`  
Seat: `ZZ–LedgerKite-N4M7 / GPT-5.6 Sol`

## Authority

- Upstream: `Ajo-contrib/soroban-ajo`
- Default branch: `master`
- Pinned head: `1b87ea7344ae3d871e54abff05eabe5113bd2956`
- Issue: `#905 No visible saga timeout/deadline`
- GrantFox live read on 2026-09-19: **Unassigned**, Apply route visible, 1 application per user, Direct GitHub comment.
- Labels: `medium`, `backend`, `Maybe Rewarded`, `GrantFox OSS`, `correctness`, `Third Campaign`.
- Upstream permissions for `@woahwhattheheck`: pull=true, push=false.
- This packet is pre-assignment research only. It does not claim provider assignment, reward, or upstream write authority.

## Current source

Exact source blobs at the pinned head:

| Path | Blob | Finding |
|---|---|---|
| `backend/src/sagas/sagaRecovery.ts` | `461046e06a66a1531d3df870b0cbfafe73ca458b` | Startup recovery selects every `in_progress` / `compensating` row, with no heartbeat-age or lease predicate. |
| `backend/src/sagas/sagaOrchestrator.ts` | `1944ca341fef9a1e7775c64204bfae88962b49e7` | Retryable steps get `MAX_STEP_ATTEMPTS = 5` per execution/resume call; no persisted global recovery-attempt budget. |
| `backend/src/sagas/sagaMonitor.ts` | `d432f4c3efc58877725bda143e73f3fdf27aea33` | Monitoring already defines a 5-minute stale-heartbeat threshold and alerts on stale nonterminal sagas. |
| `backend/src/sagas/__tests__/sagaRecovery.test.ts` | `032a998d714dacf689b34b45fa8c523504dc5eb3` | Recovery tests cover resume, missing definition, terminal skip; they do not distinguish fresh/live vs stale work or restart exhaustion. |
| `backend/src/sagas/__tests__/sagaMonitor.test.ts` | `768e5c5dc46e077fd9ed07d1334ec534cbcfa06c` | Monitor tests already prove stale-vs-fresh heartbeat behavior. |
| `backend/prisma/schema.prisma` | `a8da55bbd0adaf04f767cd1ab923b186a1e2f84d` | `SagaInstance` persists `lastHeartbeat` and indexes it, but has no recovery lease/owner or durable recovery-attempt counter. |
| `backend/package.json` | `817d678882942193301248e5dac6b6fddabb82e8` | Jest + TypeScript checks are already available. |

## Source correction

The issue premise is **partially implemented for observability, not for recovery authority**.

`sagaMonitor.ts` already calls a nonterminal saga "stuck" only when `lastHeartbeat` is older than five minutes. But `recoverIncompleteSagas()` does not consume that threshold. On every process startup it queries:

- `status in ['in_progress', 'compensating']`
- optional saga-name allowlist

and immediately calls `sagaOrchestrator.resume(...)`.

That creates two concrete correctness gaps.

### 1. Fresh/live work can be stolen by another process

In a multi-process or rolling-restart deployment, a new process can start while another process is legitimately executing a saga and has a fresh heartbeat. The new process does not check freshness or claim a recovery lease before resuming the same persisted saga.

The existing `lastHeartbeat` index is enough substrate to make default recovery stale-only; the monitor already demonstrates the desired freshness distinction.

### 2. Retry exhaustion is not durable across restarts

`MAX_STEP_ATTEMPTS = 5` limits a retryable step inside one `execute()` call. On a later `resume()`, the local `attempts` variable starts at zero again. The step log records attempts for observability, but there is no persisted recovery-attempt budget that forces repeated restart/recovery cycles to escalate.

A permanently failing retryable step can therefore receive another bounded retry set each time recovery runs instead of converging on an operator-visible terminal state.

## Required implementation contract once assigned

A safe implementation should preserve these semantics:

1. **One freshness authority.** Recovery and monitoring must derive stale eligibility from one shared/configurable policy rather than independent constants.
2. **Fresh-saga exclusion.** Default startup recovery must not resume a nonterminal saga with a heartbeat inside the live lease window.
3. **Single-recoverer claim.** Two startup processes racing on the same stale row must not both execute it. Use a transactional compare-and-set/lease mechanism or an equivalently strong database guard.
4. **Durable exhaustion.** Persist enough recovery metadata to cap repeated recovery cycles across process restarts. Once the configured attempt/age policy is exhausted, transition to `needs_reconciliation` with an explicit reason.
5. **Do not infer compensation from age alone.** Existing non-retryable/irreversible handling already routes uncertain real-world outcomes to `needs_reconciliation`. A stale heartbeat is recovery eligibility, not proof that an external effect did or did not happen.
6. **Compensating state remains resumable.** A stale `compensating` row must resume from its persisted compensation index under the same lease/exhaustion rules.
7. **Operator visibility.** Health summaries/logging must expose recovery exhaustion and lease/staleness decisions without silently changing existing terminal-state meanings.
8. **Backward compatibility.** Existing persisted rows without new metadata must have a deterministic migration/default policy; completed/failed/`needs_reconciliation` rows stay terminal.

## Hostile tests

At minimum, add deterministic tests for:

- fresh `in_progress` row: startup recovery skips it;
- stale `in_progress` row: exactly one recoverer resumes it;
- two concurrent recovery passes: one claims, one skips;
- fresh and stale `compensating` rows obey the same lease rule;
- repeated restart/resume failures consume a **durable** budget and eventually become `needs_reconciliation`;
- a successful recovery clears/releases any lease state;
- a recovery process dying after lease acquisition becomes recoverable after lease expiry;
- terminal rows remain untouched;
- monitor and recovery share the same threshold/config source;
- clock-boundary behavior at exactly-before / exactly-at / just-after stale cutoff.

Suggested focused verification from `backend/` after implementation:

```bash
npm test -- --runInBand src/sagas/__tests__/sagaRecovery.test.ts src/sagas/__tests__/sagaMonitor.test.ts
npm run type-check
```

## Assignment boundary

Do not mutate upstream for this issue until GrantFox/maintainer assignment is explicitly verified at implementation time. The live page is currently Unassigned and already has two applicant comments.
