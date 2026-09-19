# GrantFox baseline — stellar-network-builders/wavelum-frontend #67

Operation: `GFOX3-20260919-wavelum-frontend-67/R-route-a11y-ci-contract`  
Worker: ZZ-Sol-Halley-83 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `39adce49545f0114ef0ae25ad835af39de46f492`

## Provider / custody fence

- GitHub: https://github.com/stellar-network-builders/wavelum-frontend/issues/67
- GrantFox: https://contribute.grantfox.xyz/org/stellar-network-builders/repo/wavelum-frontend/issue/67
- Issue: OPEN; GitHub assignee none.
- Public GrantFox state: **Unassigned**; Apply enabled; one application per user.
- Exact Slack census immediately before TAKE found only the grouped supply root and no TAKE / PROGRESS / DONE.
- GitHub pull-request search `is:pr is:open 67` returned no matching carrier.
- Upstream connector permission: pull=true, push=false.
- This packet is source-readiness only. It does not mutate upstream source, claim assignment, or assert a fixed reward.

## Source correction

The issue's broad premise is stale in several places. Current source already has:

- Playwright and `@playwright/test`, with `npm run test:e2e`.
- `@axe-core/react` and Storybook's a11y addon in dev dependencies.
- Lighthouse CI with an accessibility score gate.
- A dedicated accessibility job in `.github/workflows/test.yml`.
- An existing Playwright visual-regression workflow and report upload.

The source-real residual is still substantial:

1. `@axe-core/playwright` is absent.
2. `eslint-plugin-jsx-a11y` is absent and the flat ESLint config has no jsx-a11y plugin/rules.
3. The only Playwright spec is `e2e/visual/home.spec.ts`; it visits only `/` and asserts one screenshot.
4. Lighthouse scans only `/en`, `/ja`, `/ko`, `/zh`; it does not crawl dashboard routes.
5. The dedicated accessibility job runs only a Tailwind contrast command and deliberately makes the command fail-open with `|| echo "Color contrast check skipped..."`.
6. The visual-regression workflow itself is also informational (`continue-on-error: true`) until matching baselines exist; it must not be mistaken for a blocking accessibility gate.

## Exact current evidence

| Surface | Blob | Current behavior |
|---|---|---|
| `package.json` | `abd989198d17220cb482e122f8e438fe7669bf6e` | Playwright + a11y-adjacent deps exist; no `@axe-core/playwright` or jsx-a11y |
| `.github/workflows/test.yml` | `a86e106c5b9b4e5aae41f00c10f0d0132dcf7e8d` | accessibility job is contrast-only + fail-open; Lighthouse separate |
| `playwright.config.ts` | `56ae4d4cd5c09cd18cb9762e1f2396a4aaa62e5f` | production webServer; Chromium; HTML/GitHub reporter; visual-oriented config |
| `eslint.config.mjs` | `84d7a2a7b33d8930fed0f4d8671c77a4a574fd46` | flat config; no jsx-a11y plugin |
| `.lighthouserc.json` | `538c4d061e21d842d255bc3f06b13d10a8fb26a4` | only four locale-home URLs; accessibility minScore 0.95 |
| `e2e/visual/home.spec.ts` | `90a7f47fafbb2e6499edfe201b072e79066971d4` | sole current Playwright page test; root screenshot |
| `i18n/routing.ts` | `e312ea8c659001cdf362c8c766f4242f4607d529` | locales en/ja/ko/zh; locale prefix always |
| `src/config/navigation.ts` | `10ae5dcf7ff7929533cf0cfefde6cb78c4068f6d` | six dashboard destinations; Admin role-gated |
| `src/components/layout/DashboardLayout.tsx` | `e2c05ec46f52fbc4ba946050fe7b2fc71a9f12e8` | Ctrl/Cmd+B sidebar; Ctrl/Cmd+K command palette |
| `src/components/layout/NavLinks.tsx` | `1130dd6f6c6ba2bd63dfaf6d92d7d1897eccc048` | primary nav + aria-current |
| `src/components/layout/MobileNav.tsx` | `87a90832dd56f31a750044277dd562fc9ecf7219` | Radix dialog, focus trap, Esc close |
| `src/components/layout/CommandPalette.tsx` | `b02dcc5ddaab90249a75539f0c8b7470f09cee87` | autofocus search, ArrowUp/Down, Enter navigation |
| `src/components/layout/Header.tsx` | `d3305d490de82d9a2d9dfd2da6fc6f10266fcbfd` | mobile menu, search, wallet, notifications, theme, locale controls |

## Canonical route matrix

Current Next.js App Router exposes one locale home template plus six dashboard templates:

| Template | Required modes | Current automated coverage |
|---|---|---|
| `/[locale]` | anonymous, 4 locales | Lighthouse all 4 locales; Playwright root screenshot only |
| `/[locale]/dashboard` | standard user | none in Playwright/Lighthouse |
| `/[locale]/dashboard/vaults` | standard user | none |
| `/[locale]/dashboard/streaming` | standard user | none |
| `/[locale]/dashboard/claims` | standard user | none |
| `/[locale]/dashboard/analytics` | standard user | none |
| `/[locale]/dashboard/admin` | admin | none |

The route manifest should be checked in as data rather than duplicated across tests. It must encode pathname, locale coverage, and required setup/role. Dynamic routes added later must fail a manifest-drift check or be explicitly excluded with a reason.

## Assignment-ready implementation contract

After provider/maintainer assignment:

1. **Dependencies and lint**
   - Add `@axe-core/playwright` and `eslint-plugin-jsx-a11y`.
   - Wire jsx-a11y into the existing ESLint 9 flat config.
   - Make the relevant a11y lint rules blocking; do not hide them behind the existing informational `lint:strict` step.

2. **One canonical route manifest**
   - Cover all seven current route templates.
   - Exercise all four locale home shells and every dashboard destination at least in the canonical locale.
   - Run locale-sensitive checks across all four locales where translated text or document language can change accessible output.
   - Include a role/setup field so Admin is tested under an admin fixture rather than silently skipped for normal users.
   - Fail when route inventory drifts without a manifest decision.

3. **Axe Playwright suite**
   - Start the production build using the existing Playwright webServer.
   - Navigate each manifest case, wait for stable/hydrated UI, then run Axe.
   - Treat WCAG A/AA violations as test failures. Do not downgrade failures to console warnings.
   - Emit machine-readable per-route results plus an HTML or human-readable aggregate.
   - Preserve route/locale/setup identity in every failure so CI output is actionable.

4. **Keyboard behavior suite**
   - Verify primary navigation is reachable/activatable by keyboard and `aria-current` follows navigation.
   - Desktop sidebar: collapse button changes `aria-pressed`; Ctrl/Cmd+B does not strand focus.
   - Command palette: Ctrl/Cmd+K opens, search receives focus, ArrowUp/Down selects, Enter navigates, Escape closes, and focus returns to a sensible control.
   - Mobile nav: hamburger opens, focus remains trapped, Escape closes, close/navigation returns focus appropriately.
   - Header controls (search, wallet affordance, notifications, theme, locale) remain keyboard reachable with non-empty accessible names.
   - Do not use a blanket “Tab N times” assertion; bind checks to named controls/landmarks and focus transitions.

5. **Blocking CI + reports**
   - Replace or repair the fail-open accessibility job. No `|| echo`, `continue-on-error`, or swallowed Playwright exit code on the blocking a11y path.
   - Install Chromium, run the dedicated axe/keyboard suite, and upload reports under `if: always()`.
   - A report-upload failure must not turn a genuine test failure green.
   - Keep visual regression, Storybook a11y, and Lighthouse as complementary layers; do not duplicate them into the new gate.

6. **Determinism / safety**
   - Use mocked/local fixtures for wallet/account state. No wallet signing, funded account, or mainnet transaction is required.
   - Disable or stabilize animation/timing sources that make focus/axe assertions flaky.
   - If a route depends on unavailable external services, inject a deterministic fixture rather than excluding the route from accessibility coverage.

## Hostile regression matrix

- Introduce a known axe violation => a11y job exits non-zero while artifact still uploads.
- Break a route manifest entry => manifest drift check fails instead of silently reducing coverage.
- Hide an interactive control from Tab order => keyboard test fails.
- Remove accessible name from icon-only header/mobile button => axe or named-control assertion fails.
- Open mobile dialog and press Tab repeatedly => focus never escapes the dialog.
- Escape closes mobile nav and command palette; focus is restored.
- Change active dashboard route => exactly the expected nav link has `aria-current="page"`.
- Non-admin fixture does not “pass” the admin route by skipping it; admin fixture must cover it.
- One localized route fails while English passes => report names the failing locale/path.
- Accessibility command unavailable or misconfigured => CI fails; no fail-open echo path.

## Suggested verification after implementation

```text
npm ci
npm run lint
npm run typecheck
npm run build
npx playwright install --with-deps chromium
npm run test:e2e:a11y
npm run test:e2e:keyboard
```

The exact new script names can differ, but the CI must invoke a dedicated blocking accessibility target rather than relying on the visual suite's current informational workflow.

## Application draft

> Applying for #67 after auditing current `main@39adce49545f0114ef0ae25ad835af39de46f492`. The repo already has Playwright visual regression, Storybook a11y tooling, Lighthouse accessibility scoring, and a contrast job, so I would build on those rather than duplicate them. The remaining gap is a blocking route-aware accessibility contract: add `@axe-core/playwright` + jsx-a11y, check in one locale/role-aware route manifest covering home + all six dashboard destinations, add axe and keyboard regressions for the existing sidebar/mobile-nav/command-palette interactions, make CI fail closed, and always upload route-scoped reports. I would use deterministic mocked wallet/auth state and no funded/mainnet path.

## Authority / reward boundary

Campaign labels and `Maybe Rewarded` indicate campaign context only. No reward is guaranteed. Upstream implementation remains gated on provider/maintainer assignment.
