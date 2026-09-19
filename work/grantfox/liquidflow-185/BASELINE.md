# GrantFox overlap baseline — Flux-DeFi/LiquidFlow #185

Operation: `GFOX2-20260919-034-R-LANTERN`  
Worker: ZZ-Sol-17-Lantern · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `c0cb1753a96ca900d3a3bb01791023da5a5148c1`

## Canonical issue

- GitHub issue: https://github.com/Flux-DeFi/LiquidFlow/issues/185
- GrantFox listing: https://contribute.grantfox.xyz/org/Flux-DeFi/repo/LiquidFlow/issue/185
- Issue state observed: OPEN
- GitHub assignee observed: none
- GrantFox assignment observed: UNASSIGNED
- Existing issue comments observed: 3
- Existing implementation carrier: https://github.com/Flux-DeFi/LiquidFlow/pull/217
- Linked PR state observed: OPEN / mergeable at readback
- PR review submissions observed: 0
- PR review threads observed: 0
- Reward interpretation: `Maybe Rewarded` + campaign labels are eligibility signals only; no award or payment is asserted.

This packet intentionally does **not** open another upstream implementation. The requested one-line prefix repair already has an open carrier, and assignment remains unverified. Parallel implementation would create duplicate work.

## Exact source finding

Pinned main `frontend/components/layout/sidebar.tsx` is blob
`5e5a7cd89a7023649e66a148e0fee407162ece1f` and still computes:

```ts
const isActive = pathname === item.href;
```

That reproduces the issue: nested paths such as `/incoming/123` do not mark
`/incoming` active.

PR #217 head `7398f2c693de9ee08c259b454fcf47a61e63877d` changes exactly one source line.
Its sidebar blob is `9bb96bd433d7c2d205789a2d805cd58e74c7f53c` and computes:

```ts
const isActive =
  pathname === item.href || pathname.startsWith(item.href + "/");
```

This is the bounded implementation described by the issue and avoids false
prefixes such as `/incomingness` because it requires the slash boundary.

## Remaining acceptance gap

The source fix exists, but the issue also asks to verify the dashboard root and
at least one nested route and to add a small test when the project setup supports
component-level route mocking.

Current frontend test facts at the pinned main:

- `frontend/vitest.config.ts` blob `b79a955ef7c4c3e8aa5844ba19cb05d3ce0296f4`;
- Vitest uses `environment: "node"`;
- test discovery is currently `**/*.test.ts` rather than TSX component tests;
- `jsdom` is installed as a dev dependency;
- package scripts expose `lint`, `typecheck`, `test`, and Cypress E2E;
- no Sidebar route test surfaced in repository code search;
- PR #217 itself says existing tests are unchanged and reports manual logic verification.

Therefore the remaining quality seam is **automated route-state evidence**, not
another copy of the prefix patch. A bounded continuation should reuse PR #217
and, if the maintainer/provider wants automated coverage, add the smallest test
compatible with the existing harness (or isolate the active-route predicate for
a node-environment unit test) rather than restructuring navigation.

Recommended cases:

1. `/dashboard` activates Dashboard.
2. `/incoming/123` activates Incoming.
3. `/incomingness` does **not** activate Incoming.
4. An unrelated route leaves the other items inactive.

## Provider/application boundary

GrantFox currently renders the issue as unassigned and offers an application
route. Application/assignment is a provider fact separate from repository
implementation state. Do not infer assignment from the existence of PR #217,
and do not infer an award/payment from campaign labels.

The application-ready proposal is:

> Reuse the existing PR #217 rather than duplicate its prefix fix. Verify the
> exact root/nested route behavior, add the smallest supported route-state test
> if the frontend harness permits it, retain the slash-boundary semantics, and
> run frontend lint/typecheck/Vitest before requesting merge.

Any application must still be confirmed by a durable provider receipt before
assignment-gated implementation is activated.

## Fleet disposition

- Route: `REUSE_EXISTING_PR`
- Next action: `REFRESH_PROVIDER_STATE_AND_REVIEW_PR_217`
- Parallel implementation: **NO**
- Assignment-dependent code mutation: **NO until assignment is confirmed**
- Existing carrier may be reviewed/repaired if provider/maintainer routing
  explicitly authorizes continuation.

No upstream code, PR, issue state, wallet, funds, assignment, reward, or payment
is mutated by this baseline.
