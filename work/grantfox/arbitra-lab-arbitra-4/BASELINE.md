# GrantFox baseline — Arbitra-Lab/Arbitra #4

Operation: `GFOX3-20260919-arbitra-4-R-ZZ-SOL-DELTA`  
Worker: ZZ-Sol-Delta · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `b98c3f7eb6ee283c97ef7bfd8917280a8197b698`

## Canonical issue/provider state

- GitHub: https://github.com/Arbitra-Lab/Arbitra/issues/4
- GrantFox: https://contribute.grantfox.xyz/org/Arbitra-Lab/repo/Arbitra/issue/4
- Issue: OPEN; assignee: none; 1 existing generic application comment
- GrantFox: Unassigned; `Apply to this issue` visible; one application per user; direct GitHub comment
- Open PR census at observation: none
- Connector permission snapshot: pull=true, push=false
- Labels include `critical`, `frontend`, `testing`, `Maybe Rewarded`, `GrantFox OSS`, and `Official Campaign | FWC26`
- Reward interpretation: possible/discretionary only. No fixed award, assignment, or payment is asserted.

This is a pre-assignment source/test-gap packet. It does not mutate upstream code or use funded/mainnet assets.

## Existing test harness

Pinned `frontend/vitest.config.ts` blob: `b3313748cb5b383f842f4afae9b02fabdcda2b69`.

Current Vitest behavior:

- environment: `jsdom`
- globals enabled
- test discovery: `**/__tests__/**/*.test.{ts,tsx}`
- setup files: `test/setup.ts` and `test/setup-tests.ts`
- alias `@` -> frontend root

The package already includes:

- Vitest 4.0.18
- Testing Library React 16.3.0
- jest-dom 6.6.3
- Playwright 1.58.2
- `@vitest/browser-playwright` 4.0.18
- `@vitest/coverage-v8` 4.0.18

So this issue does not need a test stack invented from scratch.

## Existing tests are real, but mostly outside the requested critical flows

The pinned `frontend/__tests__/` tree currently contains only these top-level groups:

- `components/error/`
- `lib/`
- `security/`
- `store/`

Observed examples:

- error boundary/provider/container component tests
- persistence/query/optimistic-update utility tests
- XSS-prevention test
- store tests for auth, error, loading, notification, selectors, and UI

The existing auth test is `frontend/__tests__/store/auth-store.test.ts`, blob `61f6841c37ca9174632c8ccc2d71ffee135ec843`. It verifies auth-store defaults, token/localStorage persistence, hydrate/corrupt-storage behavior, a development-bypass login, logout, and alias identity.

That is useful auth-state coverage, but it is **not** issue #4's requested component coverage for Stellar wallet + password authentication.

No wallet, dispute, escrow, or arbitration-specific test group appeared in the pinned `__tests__` tree. Connector code searches scoped to the test tree did not surface `wallet`, `dispute`, `escrow`, or `arbitration` matches.

## Requested UI surfaces exist on pinned main

Concrete source targets observed at the pinned SHA include:

### Authentication / wallet

`frontend/components/auth/`:
- `ProtectedRoute.tsx`
- `RoleSelectionModal.tsx`
- `WalletConnectButton.tsx`

`frontend/components/stellar/`:
- `StellarAccountBalance.tsx`
- `StellarAccountDetail.tsx`
- `StellarAccountHistory.tsx`
- `StellarAccountList.tsx`
- `StellarAccountsView.tsx`

The package depends on both `@stellar/freighter-api` and a Stellar wallets kit. Test cases must mock those transports/wallet APIs; they must not use the funded payout wallet or mainnet funds.

### User disputes

`frontend/app/user/disputes/`:
- list page
- `new/`
- `[id]/`

### Admin arbitration/dispute management

`frontend/app/admin/disputes/`:
- list page
- `[id]/`

### Contract/escrow-facing route

`frontend/app/user/contracts/page.tsx` exists and is a concrete entry point for integration-path discovery. The final implementation should trace the actual escrow service/API boundary from this route rather than inventing a parallel test-only abstraction.

## The 70% acceptance criterion is currently not measurable

Pinned `frontend/package.json` blob: `827361835145b256b32bab942a3f321583d931ba`.

Current scripts include only:

- `test`: `vitest run`
- `test:watch`: `vitest`

There is no `test:coverage` script.

The pinned Vitest config has no `coverage` block or threshold.

The frontend CI workflow runs only `pnpm test`; it does not pass `--coverage`, upload a coverage report, or enforce a threshold.

Therefore the issue's “70%+ code coverage” acceptance condition is not currently enforced or even reported by the canonical test command despite the coverage-v8 package already being installed.

## Test isolation baseline

`test/setup.ts` installs an in-memory localStorage replacement when needed.  
`test/setup-tests.ts` installs jest-dom matchers.

This is a usable base, but wallet/network/service tests should add deterministic module/transport mocks and per-test reset behavior. Do not let Freighter/Stellar/RPC/network calls escape to live services in CI.

## Narrow post-assignment implementation plan

1. Keep one Vitest-based frontend harness; do not introduce a competing runner.
2. Add component tests for `WalletConnectButton` and the actual password/login surface:
   - connect success,
   - user rejection,
   - unavailable wallet/provider,
   - disconnect/reconnect,
   - address/account rendering,
   - password validation/failure/success paths,
   - protected-route/auth-state transitions.
3. Add user-dispute route/component tests for create/list/detail/management behavior:
   - validation,
   - loading/empty/error states,
   - successful creation,
   - server error/retry,
   - state/status rendering.
4. Add admin-dispute/arbitration tests:
   - authorization/role gating,
   - queue/detail rendering,
   - action success/failure,
   - stale or invalid state handling,
   - deterministic evidence/decision fixtures.
5. Add escrow/contract integration tests by tracing the existing `app/user/contracts` service boundary:
   - mocked/local/testnet-only transaction fixtures,
   - happy path,
   - rejected signature,
   - backend/contract failure,
   - retry/idempotency behavior where the current code supports it,
   - UI state after success/failure.
6. Add a `test:coverage` command and V8 coverage configuration.
7. Define the 70% threshold precisely. Prefer global lines/functions/branches/statements unless maintainers specify a narrower interpretation; document exclusions for generated/config-only files.
8. Run coverage in the frontend CI job and retain a human-readable summary/artifact.
9. Keep tests under the existing `__tests__/**/*.test.{ts,tsx}` discovery rule unless there is a deliberate documented migration.
10. Add shared fixtures/mocks for wallet, auth user, dispute records, admin decisions, and escrow results so tests do not duplicate brittle object literals.

## Acceptance verification after assignment

Record exact commands and exit codes for:

- clean `pnpm install --frozen-lockfile`
- `pnpm test`
- `pnpm run test:coverage`
- threshold failure proof (temporary/disposable lowered-coverage fixture or config check)
- threshold success on final tree
- production build
- existing frontend CI-equivalent command
- wallet tests with network disabled/mocked
- dispute/admin tests with deterministic API fixtures
- escrow integration tests using mocked/local/testnet fixtures only

Coverage reporting must not silently omit the exact critical paths the issue names.

## Application draft

> I checked current `main@b98c3f7eb6ee283c97ef7bfd8917280a8197b698` before applying. The repo already has a solid Vitest/jsdom/Testing Library base, but the current `__tests__` tree is concentrated on stores, utilities, security, and error components. The existing auth-store tests cover persistence/hydration/dev-bypass state, not the issue's Stellar-wallet/password UI. The concrete wallet target `components/auth/WalletConnectButton.tsx`, user dispute routes, admin dispute routes, and user contracts route are present without corresponding critical-flow test groups in the current tree.
>
> The 70% requirement is also not enforceable today: `@vitest/coverage-v8` is installed, but there is no coverage script, no Vitest coverage threshold, and CI runs plain `pnpm test`. After assignment I would extend the existing harness rather than replace it: deterministic mocked wallet/password tests, user dispute + admin arbitration tests, escrow/contract integration tests with no live/funded network use, shared fixtures, and a V8 coverage command/CI threshold that explicitly includes the critical flows.
>
> I will wait for official assignment before assignment-dependent source changes.

No provider submission success is claimed in this packet.

## Authority boundary

No upstream source, assignment, wallet, funds, award, or payment is changed. All financial/wallet test planning is restricted to mocks, local fixtures, or testnet.
