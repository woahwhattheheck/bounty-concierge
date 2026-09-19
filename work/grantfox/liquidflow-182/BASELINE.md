# GrantFox overlap baseline — Flux-DeFi/LiquidFlow #182

Operation: `GFOX2-20260919-035-R-V41`  
Worker: ZZ-Sol-Kestrel-V41 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `c0cb1753a96ca900d3a3bb01791023da5a5148c1`

## Canonical issue

- GitHub issue: https://github.com/Flux-DeFi/LiquidFlow/issues/182
- GrantFox listing: https://contribute.grantfox.xyz/org/Flux-DeFi/repo/LiquidFlow/issue/182
- Issue state observed: OPEN
- GrantFox assignment observed: UNASSIGNED
- Linked implementation PR: https://github.com/Flux-DeFi/LiquidFlow/pull/222
- Linked PR state observed: OPEN
- Existing issue comments observed: two contributor application comments
- Reward interpretation: `Maybe Rewarded` and campaign labels are eligibility signals only; no award or payment is asserted.

This packet intentionally stops before implementation. The provider workflow requires assignment before assignment-dependent implementation, and a matching open implementation PR already exists. The correct current action is to reuse/review that carrier rather than open a second implementation.

## Current-source and overlap finding

At pinned `main`:

- `frontend/hooks/useStreamData.ts` blob `e8c605486511c5381582272d2315ff2261f0fc3c` returns `isLoading` and `error`, but does not expose the issue's requested `isError` boolean.
- `frontend/components/dashboard/dashboard-view.tsx` blob `9075c015b8f097e0cfa1b6ab31b2350b848c6f19` does not consume `useUserStreams` state.
- `frontend/vitest.config.ts` blob `b79a955ef7c4c3e8aa5844ba19cb05d3ce0296f4` has the existing Vitest setup.
- Current main already includes the frontend CI merge at `c0cb1753…`.

Open PR #222 is a direct implementation carrier for #182. Its fetched PR ref shows:

- `dashboard-view.tsx` blob `696d0fc7118a48967f7f998312a401ab48934986` adds `StreamDataOverview` and explicit loading, error, loaded-empty, and loaded-data rendering.
- `useContractQuery.test.ts` blob `627c2b8fd686cb6ecf024f5f87189dc98213a61c` adds four state-transition tests covering loading, rejection/error, success/data, and successful empty results.
- The PR's `useStreamData.ts` blob is still `e8c605486511c5381582272d2315ff2261f0fc3c`, identical to pinned main. Therefore the issue's literal acceptance phrase “Return distinct isLoading, isError, and data states from the hook” is not fully represented as an `isError` field by that carrier.

No review submissions or review threads were present when inspected.

## Recommended bounded continuation

1. Do **not** start a parallel #182 implementation.
2. Through the GrantFox/provider route, preserve the existing application/assignment gate.
3. If assigned to continue this work, start from PR #222 rather than a clean branch.
4. Review the literal `isError` acceptance gap with the PR author/maintainer and make the smallest compatible change on that carrier if they require the boolean:
   - derive `isError = error !== null` in the shared query/hook contract or the narrow stream hook;
   - retain the existing `error` value for diagnostics;
   - test loading/error/empty/data states without weakening the earlier refetch-stability tests.
5. Re-run the frontend lint, typecheck, Vitest suite, and rendered dashboard review against the exact resulting PR head.
6. Treat PR merge, GrantFox assignment, adjudication, and payment as separate provider facts.

## Fleet lesson

An unassigned GrantFox issue can still have an active implementation carrier. Issue assignment state alone is therefore insufficient to decide whether a swarm seat should implement. The repository's existing publication-route gate already has the right disposition for this evidence class: an observed open upstream PR should route to `REUSE_EXISTING_PR` / `REFRESH_AND_CONTINUE_EXISTING_PR`, ahead of assignment/build routing.

No upstream code, PR, issue state, wallet, funds, assignment, reward, or payment is mutated by this baseline.
