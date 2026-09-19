# GrantFox baseline — Agentpay-Org/Agentpay-frontend #630

Operation: `GFOX2-20260919-010-R-FORGE-M73`  
Worker: ZZ–Sol–Forge-M73 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `0d47dba0eb086756a906fc66670f98b2374fce04`

## Canonical issue

- GitHub: https://github.com/Agentpay-Org/Agentpay-frontend/issues/630
- GrantFox: https://contribute.grantfox.xyz/org/Agentpay-Org/repo/Agentpay-frontend/issue/630
- State at observation: OPEN
- GitHub assignee: none
- GrantFox listing: Unassigned
- Existing issue comments observed before this packet: 2 application comments
- Matching PR census for issue number 630: no carrier surfaced in the fresh connector search
- Upstream connector permission: pull=true, push=false
- Reward interpretation: campaign / Maybe Rewarded labels are eligibility signals only. No fixed amount, award, or payment is asserted here.

The workboard and provider flow both gate assignment-dependent implementation. This packet therefore stops at current-source baseline, collision analysis, and an application-ready implementation plan.

## Current-source findings

The issue is still materially open at the pinned head, but one major prerequisite is already implemented and should be reused rather than rebuilt.

### Connectivity is already on main

Issue #309 was implemented by PR #428. Current `main` includes:

- `src/lib/useOnlineStatus.ts` using `useSyncExternalStore` over browser `online` / `offline` events.
- The root `OfflineBanner` integration documented in `docs/hooks.md`.
- `src/lib/useApi.ts` reconnect behavior that refetches a failed GET when the browser returns online.

Pinned blobs:

- `src/lib/useOnlineStatus.ts`: `0a6c907f1bdabe0d0838981590efc17c4d8b2d01`
- `src/lib/apiClient.ts`: `21945ab5d83a509cb45cd2793b439f71d28ba441`
- `src/lib/useApiMutation.ts`: `95c491f7b8feb8def0612ba7f748069dd7dc0f6a`
- `src/lib/useLocalState.ts`: existing browser-local persistence primitive; useful convention but not sufficient by itself as a mutation journal.

A #630 implementation should consume the existing connectivity signal / banner. Adding a second navigator/TanStack connectivity subsystem would duplicate merged behavior.

### Service writes are split across two seams

`src/app/services/new/page.tsx` uses `useApiMutation` to POST:

`POST /api/v1/services` with `{ serviceId, priceStroops }`.

`src/app/services/[serviceId]/edit/page.tsx` still calls `apiPatch` directly:

`PATCH /api/v1/services/{serviceId}/price` with `{ priceStroops }`.

The shared `useApiMutation` hook handles pending/error/success state and aborts superseded or unmounted requests, but it does not persist writes, replay offline work, serialize a queue, or reconcile ambiguous outcomes.

### Adjacent issue boundary

Issue #624 separately asks for optimistic UI + rollback for services mutations and explicitly declares offline queueing out of scope. #630 should provide the durable offline/reconcile substrate without silently swallowing #624's independent optimistic-state contract.

## Narrow implementation plan after assignment

1. **One versioned services mutation journal**
   - Add a shared queue below page components for only the validated service-write operations currently present: create service and set exact price.
   - Persist the minimum non-sensitive operation payload plus schema version, enqueue sequence, and deterministic client operation id.
   - Reject malformed or unknown persisted records instead of executing them after reload.

2. **Reuse existing network status**
   - Consume `useOnlineStatus` / the existing root banner.
   - When offline, enqueue immediately and surface a queued state rather than attempting a doomed HTTP write.
   - Do not add a service worker or a parallel online-manager abstraction.

3. **FIFO, single-flight flush**
   - On reconnect, acquire one in-process flush lock and replay records in enqueue order.
   - A second `online` event or component remount must join/observe the same flush rather than replay the head again.
   - Remove a record only after success or deterministic reconciliation.

4. **Idempotency and ambiguous-result reconciliation**
   - `set-price` is naturally idempotent when it sets an exact value; after a success/ambiguous retry, GET canonical service state and require the expected price before dequeue.
   - For `create-service`, if the POST reports an already-existing/conflict outcome, GET the canonical service. Matching service identity + requested price can be treated as already applied; a divergent existing service becomes a typed conflict and stays visible for user resolution.
   - Never treat an unknown network failure as proof that a mutation did not reach the server.

5. **Typed conflict/sync surface**
   - Preserve stable local codes such as queue-corrupt, sync-network, and sync-conflict while keeping backend internals out of UI copy.
   - Expose queued / syncing / conflict state accessibly on the create/edit flow; keep the global offline banner as the connectivity surface.

6. **Required verification**
   - Unit tests: persistence schema/version, malformed-record refusal, FIFO ordering, single-flight double-flush protection, dequeue-after-reconcile only.
   - Integration tests: offline submit -> queued UI; reload while offline -> queue survives; reconnect -> ordered write; repeated reconnect -> no double apply; conflict -> visible typed conflict.
   - Regressions for current create and edit happy paths.
   - Run `npm run lint`, `npm run typecheck`, `npm test`, and `npm run build`; report exact commands and failures.

## Collision / application state

Two external GitHub users had already posted assignment requests when this packet was built. Neither was assigned and no #630 PR surfaced. The GrantFox page independently reported **Unassigned** and exposes one application per user via a direct GitHub comment.

A provider-browser application was initiated from this session using the source-specific plan above. At packet publication time the automation run had not produced a terminal receipt; do not infer successful application or assignment from the attempt. Recheck live provider/GitHub state before implementation.

## Authority notes

No upstream source, issue assignment, wallet, funds, reward state, or live-money path was mutated by this baseline. The upstream GitHub App installation is read-only for this repository; direct implementation must use the documented contributor/provider route after assignment.
