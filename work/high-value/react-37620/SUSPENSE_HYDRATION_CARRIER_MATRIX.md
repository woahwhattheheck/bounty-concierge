# React #37620 — Suspense hydration carrier acceptance matrix

Owner: **ZZ Zeta Relay / GPT-5.6 Sol**  
Issue: `react/react#37620` — open/unconfirmed; BountyHub bot advertises $100 from a third party, not the reporter.  
Current main source read at review time: `packages/react-reconciler/src/ReactFiberBeginWork.js` blob `2de9437bccce48bd9c2224c3175d9797f208e9d2`.

Active resolving PRs reviewed:
- #37641 head `f9001280d941c1c9786586958c462b7e8ea91f6a`
- #37644 head `2d26e0883b86bfb0167d7ac895164f91db1d5f3a`
- #37648 head `6a45f5fbd63f636e560bf6e7769d2b1625fe36d1`
- #37654 head `b0ae2efbea6578ec5f3fd8729df730c3e5fc6b12`

Historical reference:
- #24236 merged a broad "do not recreate same fallback if hydration suspends" strategy.
- #24434 reverted it because of a WWW "late mutations" regression whose mechanism was not documented publicly.

No fifth implementation should be opened from this review.

## Root cause confirmed in current main

Current `updateDehydratedSuspenseComponent` has a retry path where the boundary has left dehydrated mode, primary content suspends again, and React calls `mountSuspenseFallbackAfterRetryWithoutHydrating`. That replaces the server-emitted fallback with a newly mounted client fallback. The reporter's `use(browser())` → client `use(promise)` sequence enters exactly this path, so equivalent fallback pixels arrive on a different DOM node and animations/state restart.

## Two competing solution classes

### A. Preserve the dehydrated fragment as the fallback branch — #37644 / #37648 / #37654

These three implementations are variations on the same narrow mechanism:
- cancel the scheduled deletion of the existing `DehydratedFragment`;
- mount the primary branch hidden;
- retain the dehydrated fragment as the fallback sibling;
- add DOM-node-identity tests.

This directly addresses the reported animation restart with a small reconciler diff.

**Missing acceptance dimension:** these variants preserve *server markup*, not a hydrated fallback component tree. Their diffs contain no fallback hydration path (`reenterHydrationStateFromDehydratedSuspenseInstance` / equivalent) and no interactive/effect fallback tests. At the reviewed heads, all three have zero occurrences of `onClick`, `useEffect`, or `InteractiveFallback` in their changes.

That matters because keeping the same DOM node is not sufficient for a React fallback that has state, effects, refs, context, or handlers. A server-rendered button can remain visually present yet lack the mounted client component semantics expected during the pending interval.

Before accepting this solution class, add a regression where:
1. SSR emits an interactive fallback because `use(browser())` bails out.
2. `hydrateRoot` retries primary content and it suspends on a client promise.
3. The fallback DOM node identity remains unchanged.
4. A fallback `useEffect` has mounted exactly once.
5. Clicking a fallback button updates state before the primary promise resolves.
6. Resolving the primary promise reveals content and cleans up the fallback effect exactly once.

If that test cannot pass without hydrating the fallback, node-preservation alone is incomplete.

### B. Hydrate the existing fallback tree — #37641

#37641 takes the larger approach: it re-enters hydration for the server fallback, represents transient fallback-hydration state, and updates completion/commit/DOM-marker handling. Its test set explicitly includes interactive fallback state/effect lifecycle, Context, `useId`, unmount, nested fallbacks, mismatch/error recovery, cancellation, and cross-root error isolation.

This addresses the missing component-semantics dimension above, but it changes nine files (+1263/-6 at the reviewed head) and therefore carries a wider commit/hydration ordering surface. The author explicitly notes that the old #24236 WWW late-mutation regression cannot be reproduced from public information.

## Acceptance fence that discriminates the carriers

A maintainer-facing regression should test **both DOM continuity and React semantics**, not only pixels/node identity:

- exact server fallback DOM node survives the client re-suspend;
- fallback event handler works while the primary promise is pending;
- layout/passive effect mount + cleanup counts are balanced;
- fallback state survives user interaction;
- fallback Context and `useId` hydrate consistently;
- unchanged boundary can preserve the fallback; changed fallback props/context are not frozen indefinitely;
- primary resolution removes fallback exactly once and reveals the primary tree;
- nested Suspense markers are not over-removed;
- mismatch/error in fallback hydration fails locally into established client-render recovery;
- abandoned/interrupted hydration attempts do not leak queued errors to another root;
- a mutation-order regression modeled after the #24236/#24434 risk is exercised in WWW-modern/classic gates if maintainers can provide the internal reproducer.

## Suggested review route

Do not add another bounty carrier. Ask maintainers to choose between:
- the narrow "preserve inert dehydrated fragment" approach **only if** the interactive/effect regression above passes under intended semantics; or
- the fallback-hydration approach, with special scrutiny on commit ordering / late mutations because of #24434.

The fastest useful contribution to existing PRs is the interactive-fallback acceptance test. It distinguishes whether a two-file node-preservation patch is actually sufficient without prejudging the larger nine-file implementation.

No BountyHub claim, sponsor acceptance, or payout is implied.
