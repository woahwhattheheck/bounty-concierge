# PayD #457 — withdrawal error-integrity baseline

Status: source-audited; GrantFox/maintainer assignment required before upstream implementation.

## Target

- Upstream: `Protocol-Guild/PayD`
- Issue: #457 — **withdrawal.ts swallows all backend errors and silently falls back to fabricated mock data**
- Upstream source generation: `main@af5c348e83033ed3340e589b68e8554f0303060e`
- GitHub issue state at census: OPEN, unassigned, 2 comments
- GrantFox live page at census: Apply enabled, 1 application per user, Direct GitHub comment, Assigned to **Unassigned**
- Exact same-day Slack `PayD` + `457` census before TAKE: 0 prior results
- Upstream permission from the connected account: pull=true, push=false
- Reward truth: Maybe Rewarded / GrantFox OSS / Third Campaign. No amount or award is asserted here.

This is a pre-assignment source/application packet. It performs no upstream source mutation, provider application, wallet/payment action, assignment, award, or reward claim.

## Source truth

The issue premise is still exact on current main, and the current component contract exposes one additional false-success seam that an implementation must not miss.

### All four service methods fabricate success after transport/auth/server failure

`frontend/src/services/withdrawal.ts` (blob `633d2dcfff23edffa900d4501d387999817b6906`) catches every axios failure:

- `getAvailableAnchors()` returns three hard-coded mock anchors;
- `initiateWithdrawal()` invents a random transaction ID, anchor URL, and `pending_user_transfer` status;
- `getTransactionStatus()` fabricates a pending transaction;
- `cancelWithdrawal()` suppresses the failure and logs a mock cancellation success.

That means network errors, 401/403 responses, validation failures, and 5xx responses can be converted into plausible payment-flow success data.

### The hook is already the correct UI error boundary

`frontend/src/hooks/useWithdrawal.ts` (blob `09dd0fc3b2be90f3bdc8ffab8556d940325dd7ea`) already catches failures from all four service calls and persists a user-visible `error` string.

- anchor-load failure clears loading and records an error;
- initiation failure records the error and returns to `enter_amount`;
- status polling records the error without replacing the transaction with fake status;
- cancellation failure records the error and leaves the current transaction intact.

Therefore the service layer does not need a second “resilience” fallback. It should reject with the real/typed failure and let this hook own display state.

### Current component calls its success callback after failed initiation

`frontend/src/components/WithdrawalFlow.tsx` (blob `a19daaa65386dd90f265226db1a5d335faa3c11b`) contains:

```ts
await initiateWithdrawal(destinationType, destinationDetails);
if (state.step !== 'failed') {
  onSuccess();
}
```

This is not a reliable success test:

1. the callback closes over the pre-await render's `state.step` (normally `confirm`);
2. the hook's initiation failure path sets `step: 'enter_amount'`, not `failed`;
3. React state updates are asynchronous, so this closure is not refreshed before the conditional runs.

The current parent `frontend/src/pages/EmployeePortal.tsx` (blob `28075699ed06b95627f63336a3de32bc22f73189`) wires `onSuccess` to `refreshData()`. Today this mostly causes an incorrect success-side refresh, but the contract is semantically false-success and is unsafe to leave behind while fixing the payment error path.

A source-aligned repair should make initiation return an explicit success value/result (or move the success callback into the actual success branch) rather than checking React state immediately after an awaited callback.

## Test seam

`frontend/package.json` (blob `33e58d67eca11b200b6988835b1f8450f179d2c2`) already depends on `vitest`, but exposes no frontend unit-test script. Existing frontend automated coverage is Playwright E2E.

A bounded implementation can use both layers without redesigning the frontend:

1. **Service unit tests**
   - add a runnable Vitest script/config only if needed by repo conventions;
   - mock axios rejects for network, 401, validation/4xx, and 500 cases;
   - assert every service method rejects and never emits mock anchors/IDs/status/success.

2. **Flow/error integration**
   - use the existing Playwright request interception or another repo-native browser seam;
   - force anchor-load and initiate endpoints to fail;
   - assert the modal renders the error/retry state;
   - assert no fake transaction ID / interactive URL appears;
   - assert the success callback path is not executed for failed initiation.

3. **Positive path**
   - preserve successful backend responses exactly;
   - verify real anchors render, initiation enters processing, real status is retained, and real cancellation succeeds.

## Error policy

Do not erase axios context by replacing every failure with the same generic `Error`. Either propagate the original axios error to the hook or map it into a small typed frontend error that preserves safe status/category information. Never expose secrets or raw backend internals in UI text.

The minimum semantic categories worth preserving are:

- network/unreachable;
- authentication/authorization;
- validation/client error;
- server failure;
- cancellation/status-specific failure.

Exact copy can remain generic; the key invariant is that a backend failure cannot become a fabricated success object.

## Acceptance map

A correct assigned implementation should prove all of the following:

- none of the four service methods returns mock data after a real backend error;
- failed anchor discovery produces a visible error, not a mock anchor list;
- failed initiation produces a visible error and no transaction/interactive URL;
- failed status polling does not replace the last real transaction with fabricated pending data;
- failed cancellation does not pretend cancellation succeeded;
- the component success callback is invoked only after a real successful initiation;
- 401/4xx, 500, and network failures are covered explicitly;
- successful backend behavior is unchanged;
- frontend build/lint and the chosen focused test commands pass.

## Assignment fence

Re-census GrantFox assignment, issue comments, current `main`, and issue-linked PRs immediately before implementation. The connected account has pull-only access to upstream. Do not mutate upstream source from this packet alone.
