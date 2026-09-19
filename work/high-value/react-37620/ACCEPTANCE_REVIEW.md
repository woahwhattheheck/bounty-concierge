# React #37620 / PR #37654 acceptance review

Date: 2026-09-19  
Reviewer seat: ZZ-Sol-Cairn-519 / GPT-5.6 Sol  
Upstream: `react/react`  
Issue: #37620 — "Bug: Suspense fallback remounts on hydration after use(browser()) + client use(promise)"  
Existing carrier: PR #37654 by `Jr-kenny`  
Reviewed exact head: `b0ae2efbea6578ec5f3fd8729df730c3e5fc6b12`  
Base recorded by PR: `71f725593739d2cb5866a282a1075d581831722f`

## Economics and ownership

The issue has a BountyHub bot comment advertising **$100.00**, created by `devlootxyz-bounties`, with the documented claim route "submitting a pull request that solves this issue." This review does **not** claim that bounty: the existing implementation PR belongs to `Jr-kenny`. Do not open a duplicate carrier merely to compete for the same $100.

Upstream repository access for the connected GitHub installation is pull-only. Two attempts to submit this review through the native pull-request review endpoint returned exactly:

`403 Resource not accessible by integration`

That is an installation-scope result, not evidence that the human GitHub account lacks review permission.

## Current provider state

- Issue #37620: OPEN, label `Status: Unconfirmed`.
- PR #37654: OPEN, non-draft, GitHub reports mergeable=true.
- CLA bot first requested a Meta CLA, then confirmed it was signed.
- Human PR reviews: 0.
- Review threads: 0.
- Exact-head GitHub Actions:
  - `(Runtime) ESLint Plugin E2E` run 35301193142: completed / `action_required`
  - `(Runtime) Build and Test` run 35301193161: completed / `action_required`
  - `(Shared) Lint` run 35301193204: completed / `action_required`
- Combined commit status list is empty.

Therefore the PR is **not hosted-green**. Its body reports local `ReactDOMFizzServer-test` 187 passing plus Flow clean, but those are author-reported results until upstream Actions are approved/executed.

## Source review

The patch is focused to two files:

1. `packages/react-dom/src/__tests__/ReactDOMFizzServer-test.js`
2. `packages/react-reconciler/src/ReactFiberBeginWork.js`

The intended behavior is reasonable: for the recoverable `use(browser())` Suspense digest, preserve the server-rendered fallback DOM when the first client retry suspends again, so identity/animation state is not lost. The patch is intentionally narrower than the 2022 change in #24236 because it gates preservation on `REACT_RECOVERABLE_DIGEST`.

### STOP: fallback-prop update regression is not covered

There is a concrete regression seam inherited from the exact class that made the 2022 change risky.

PR #24236 did not merely test preserved fallback identity. It explicitly tested that if an **urgent root update changes fallback props while the primary is still suspended**, React must recreate the fallback. PR #24434 later reverted #24236 after a WWW "late mutations" regression and disabled those tests.

On #37654, after the new browser-digest path preserves `current.child` as a `DehydratedFragment`, a later suspended update enters the normal update path:

`updateSuspenseComponent -> updateSuspenseFallbackChildren(... nextFallbackChildren ...)`

The patch changes `updateSuspenseFallbackChildren` so that when the current fallback is a `DehydratedFragment`, it does:

```js
fallbackChildFragment = createWorkInProgress(
  currentFallbackChildFragment,
  null,
);
```

That branch does not consume `fallbackChildren` / `nextProps.fallback`. The existing update path then leaves the boundary in `SUSPENDED_MARKER` state and bails out. This creates a plausible stale-props bug: an urgent `root.render()` that changes the fallback while the client promise remains unresolved can keep the old server fallback DOM instead of rendering the new fallback.

This is not hypothetical scope creep: it is the safety case that #24236 explicitly preserved before #24434 reverted the larger behavior.

## Required regression before acceptance

Add an `enableBrowserAPI` test with this sequence:

1. Server render through `use(browser())` with `fallbackText="Loading"`.
2. Hydrate; client primary suspends on a client-created promise.
3. Assert the server fallback DOM node is still the same node.
4. **Before resolving the promise**, issue an urgent:
   `root.render(<App fallbackText="More loading" />)`.
5. Flush the update.
6. Assert the DOM now contains `More loading`.
7. Assert the fallback node is **not** the original dehydrated node.
8. Resolve the primary promise and assert primary content replaces the fallback.
9. Preserve a companion transition case with unchanged fallback props to prove non-urgent/unchanged updates do not cause needless remounting.

If that urgent-update test passes on the existing implementation, the main source concern is closed. If it fails, the implementation needs a condition that preserves the dehydrated fallback only while the fallback representation is still valid for the pending props.

## Disposition

**SOURCE HOLD / EXISTING CARRIER — DO NOT DUPLICATE.**

This is not a rejection of the approach. The digest gate is materially narrower than #24236, and the core node-identity tests cover the reported symptom. But the missing changed-fallback-props regression sits directly on the prior late-mutations safety boundary, so the current head should not be described as acceptance-ready solely from its 187 local tests.

Next useful action is one authenticated upstream-user review containing the regression request above, or a successor commit on PR #37654 adding the test/fix. Do not create a second bounty PR unless the current carrier is abandoned or the bounty sponsor explicitly authorizes another paid slot.
