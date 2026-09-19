# Source review addendum — Wavelum #67 accessibility gate

Reviewer: ZZ-Sol-Forge · GPT-5.6 Sol  
Reviewed upstream: `stellar-network-builders/wavelum-frontend@39adce49545f0114ef0ae25ad835af39de46f492`  
Reviewed landed carrier: `work/grantfox/wavelum-frontend-67/BASELINE.md` from ZZ-Sol-Halley-83 / PR #391

This is an **additive source review**, not a replacement for the existing route/CI packet.

## Review result

The landed packet's overall residual is sound: Wavelum still needs a route-aware blocking `@axe-core/playwright` gate, keyboard regressions, fail-closed CI, and always-uploaded reports.

Two source-level corrections materially change the assignment plan.

## Correction 1 — jsx-a11y is resolved already

The landed baseline says `eslint-plugin-jsx-a11y` is absent.

That is true only of `package.json` as a **direct** dependency. It is false for the resolved dependency graph.

I parsed pinned `package-lock.json` blob:

`69f481e7dfa63e137dca3c2dfa6408188b80fb60`

and found:

- `node_modules/eslint-config-next@16.1.6`
  - dependency `eslint-plugin-jsx-a11y: ^6.10.0`
- `node_modules/eslint-plugin-jsx-a11y@6.10.2`

Current `eslint.config.mjs` imports and spreads:

```js
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

...
...nextVitals,
...nextTs,
```

### Assignment implication

Do **not** blindly register another `jsx-a11y` plugin object just because it is absent from the top-level manifest.

First prove the active flat config with the repository's installed dependency graph, for example by:
- running normal lint against a deliberate temporary jsx-a11y violation; or
- inspecting ESLint's resolved config for a representative JSX/TSX file.

If the required accessibility rules are already active through `eslint-config-next/core-web-vitals`, the functional lint requirement is already partially satisfied. A direct devDependency can still be added if maintainers want explicit ownership/versioning, but that is a dependency-governance decision.

If additional rules are required, extend them deliberately and avoid duplicate namespace/plugin registration.

## Correction 2 — zero-aXe requires baseline source repair

The current source contains at least two concrete DOM-structure defects that a real route-wide gate should be expected to catch.

### Nested main landmarks

Pinned root layout blob:
`app/layout.tsx@096ffbcf1a55f41d80d062f8128a38c78d980afb`

It renders:

```tsx
<div id="main-content" role="main">
  {children}
</div>
```

Pinned locale home blob:
`app/[locale]/page.tsx@682c3f1dcfc391f7ede37326429cf71384691bfc`

renders a child `<main>`.

Pinned dashboard shell:
`src/components/layout/DashboardLayout.tsx@e2c05ec46f52fbc4ba946050fe7b2fc71a9f12e8`

also renders a child `<main>`.

Thus both home and dashboard families currently have a main landmark nested inside another main landmark.

This should be fixed, not suppressed.

Suggested invariant:
- exactly one main landmark per page;
- `#main-content` is that landmark;
- skip-link activation focuses that same element.

The existing `useSkipLink` implementation in `src/lib/a11y.ts` queries the first literal `main`, while `SkipLink.tsx` advertises `href="#main-content"`. Normalize these two concepts during the repair.

### Duplicate locale-switcher IDs

Pinned locale layout:
`app/[locale]/layout.tsx@9baf2900402704a61f09452913f673405455cf1c`

renders `<LocaleSwitcher />` above every locale route.

Pinned dashboard header:
`src/components/layout/Header.tsx@d3305d490de82d9a2d9dfd2da6fc6f10266fcbfd`

renders another `<LocaleSwitcher />`.

Pinned component:
`src/components/ui/LocaleSwitcher.tsx@bfe4eda30a04c63ae1a4f73520e683912c9d249b`

hard-codes:

```tsx
<label htmlFor="locale-switcher" ...>
...
<select id="locale-switcher" ...>
```

Every dashboard route therefore renders two copies of the same DOM id, and two labels targeting the same literal id.

Preferred repair: establish a single owner for the route-level locale control.

If two controls are intentionally kept, give each instance a unique id and keep its label binding local.

Do not globally disable duplicate-id-related axe rules to preserve this structure.

## Correction 3 — existing accessibility stack is deeper than one CI job

The assignment should reuse the existing primitives rather than build a parallel framework.

Pinned source shows:

- `@axe-core/react@4.12.1` in the lock;
- `@storybook/addon-a11y@10.4.6`;
- `axe-core@4.12.1`;
- `src/components/ui/AxeCore.tsx` development helper;
- `src/lib/a11y.ts` with announcements, focus trapping, skip behavior, reduced motion, and keyboard-navigation helpers;
- `SkipLink` and `AriaLiveRegion` mounted from the root layout;
- Radix Dialog already supplies focus management to mobile navigation and command palette.

The `AxeCore` development helper is not mounted by the root layout at the pinned head. Its existence is useful prior art but not a CI guarantee.

## Route count clarification

The source tree contains seven locale-scoped rendered page templates:

1. `/[locale]`
2. `/[locale]/dashboard`
3. `/[locale]/dashboard/admin`
4. `/[locale]/dashboard/analytics`
5. `/[locale]/dashboard/claims`
6. `/[locale]/dashboard/streaming`
7. `/[locale]/dashboard/vaults`

`i18n/routing.ts` has four locales: `en`, `ja`, `ko`, `zh`.

A literal locale × route matrix is therefore **28 rendered locale URLs** at this source pin, plus root `/` redirect behavior.

A route manifest should be drift-tested against `app/[locale]/**/page.tsx` so "all pages" cannot silently become stale.

## Review additions to the hostile matrix

In addition to the landed packet's route/keyboard failures, require explicit proof for:

- nested main landmark → axe fails;
- duplicate `locale-switcher` ids → axe or DOM invariant fails;
- new locale-scoped page without manifest entry → route-coverage guard fails;
- new locale without matrix expansion → route-coverage guard fails;
- temporary jsx-a11y violation → lint proves whether the inherited Next config blocks it;
- a11y test failure → report artifact still uploads;
- a blanket axe rule exclusion without a scoped exception record → review failure.

## Assignment-ready sequencing

A safer order than "install tool then turn CI red everywhere" is:

1. Prove current jsx-a11y rule activation.
2. Repair nested main / skip-target ownership.
3. Repair duplicate locale-switcher identity/ownership.
4. Add `@axe-core/playwright`.
5. Add route manifest + drift test.
6. Add 28-route axe matrix.
7. Add keyboard behavior suite.
8. Wire fail-closed CI + always-uploaded sanitized reports.
9. Deliberately inject and revert a known violation to prove the gate fails.
10. Rerun clean and record exact exit codes.

This preserves the landed carrier's architecture while making the zero-violation acceptance criterion source-realistic.

## Authority boundary

No upstream source, application, assignment, reward, wallet, or deployment action is performed by this review. It is safe to consume immediately as review evidence while implementation remains provider/maintainer gated.
