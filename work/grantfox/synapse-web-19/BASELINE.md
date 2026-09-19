# GrantFox source baseline — Synapse-bridgez/synapse-web #19

Operation: `GFOX3-20260919-synapse-web-19-R-test-ci-map`  
Worker: ZZ-Sol-Bounty-Ranger · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `48cdca3264161daeb735383b7bd01f14474bcd1f`

## Provider / authority fence

- GitHub issue: https://github.com/Synapse-bridgez/synapse-web/issues/19
- GrantFox listing: https://contribute.grantfox.xyz/org/Synapse-bridgez/repo/synapse-web/issue/19
- GitHub issue is OPEN and GitHub-unassigned at the pinned observation.
- One GitHub application comment is present; no GrantFox assignment-bot comment is present on #19.
- Focused open-PR searches for #19, Playwright/e2e, Sentry/error tracking, coverage, and TxDetailModal surfaced no implementation carrier.
- The connected GitHub installation reports upstream permissions `pull=true`, `push=false`.
- The public GrantFox listing was not independently readable through the public web index in this seat, so current provider-side assignment/apply eligibility is **not claimed** here.
- No upstream source, provider application, wallet, payment, award, or reward state was mutated by this worker.

This is pre-assignment source research only.

## Scope has materially drifted since the issue was written

Issue #19 says there is no test suite or CI. That is no longer true on current main.

Current `package.json` blob `2e5980a5cafe0308c18453f758376af814f8c0ee` already contains:

- `vitest`;
- `@testing-library/react`;
- `@testing-library/jest-dom`;
- `jsdom`;
- `npm test` -> `vitest run`.

Current `.github/workflows/ci.yml` blob
`0e566fbd3b098a4138758a66c2780a85650e31f7` already runs:

1. `npm ci`;
2. `npm run lint`;
3. `npx tsc --noEmit`;
4. `npm run test`;
5. `npm run build`.

Issue #41 is CLOSED/completed, and its CI carrier #60 supplied the base workflow.
Later PR #79 supplied the first Vitest suite. Its PR description reports 31 passing tests
across five files; that is **historical author evidence**, not a fresh execution claim by this
worker.

## Current exact test surface

Pinned current-main test/config blobs:

- `vitest.config.ts` — `f0a0462dbbad80f7b638534b19136ac718b5ea1c`
- `lib/utils.test.ts` — `33e2a95e5c55cf9afde17a787893ed074481af44`
- `lib/soroban/args.test.ts` — `09497cb9a3c677a275af35d3569a2d5177b7e193`
- `lib/soroban/transactionMerge.test.ts` — `1fab546c22eca6f09b2d472518425cd933603caf`
- `lib/wallet/storage.test.ts` — `7ac08c404e54cd2e3da2680f171a23f9271c997d`
- `components/ui/Badge.test.tsx` — `9f94d8214673aec83cb7fe2adbd62c537033f5f1`

Already-covered behavior includes:

- `shortId`, `elapsed`, and `formatAmount`;
- ScVal argument encoding/round-trip checks;
- wallet-id localStorage helpers;
- event merge / native transaction projection;
- Badge labels and status colors.

An assigned #19 implementation should preserve these tests instead of rebuilding them.

## Residual #19 gaps on current main

### 1. No Playwright / e2e contract

Current `package.json` has no Playwright dependency and no `test:e2e` script.
Focused current-repository/open-PR searches surfaced no Playwright/e2e carrier.

The issue's requested mocked wallet + mocked RPC lifecycle remains a real gap.

### 2. No coverage threshold

`vitest.config.ts` has no coverage provider or threshold configuration, and current
`package.json` has no coverage script/provider. Issue #19 requires a threshold for `lib/`,
but does not specify a numeric percentage. Do not invent a provider requirement or threshold
as if it came from the issue; choose and document the project policy in the assigned PR.

### 3. No error-tracking SDK / boundary

Current package dependencies contain no Sentry/error-tracking SDK. Focused source/open-PR
searches surfaced no Sentry/error-tracking carrier.

Current RPC/signing helper `lib/soroban/contract.ts` blob
`647ff977cf31b3bf82183e7bb0915f615c6324d5` throws ordinary errors for failed
simulation/submission. UI call sites catch and toast them locally. There is no centralized
unhandled RPC/signing telemetry path.

Any tracking implementation must scrub secrets, signed transaction material, callback
secrets, and other sensitive wallet/session payloads.

### 4. Component integration coverage is still shallow

The only current Testing Library component test is the Badge surface above. There is no
current `TxDetailModal`, `TransactionsTab`, or wallet-provider component test.

Pinned integration-relevant blobs:

- `components/transactions/TxDetailModal.tsx` —
  `7d2715f49e5266bda8ce5a380eb4faa1731a8f17`
- `components/transactions/TransactionsTab.tsx` —
  `98b0aff454fc549e36771e296c6512c1cb1f5b76`
- `lib/wallet/WalletProvider.tsx` —
  `c2d3adab7fcf79e9e87cc20df0707ebd7519db02`

### 5. #14's intended modal state machine is not yet enforced in the current component

Issue #14 requires legal transitions such as PENDING -> PROCESSING -> COMPLETED/FAILED and
blocking terminal/illegal transitions.

Current `TxDetailModal.tsx` renders START PROCESSING, COMPLETE, and FAIL for every status;
those buttons are disabled only while another action is pending. It therefore does **not**
currently encode the full #14 legal-state matrix.

That matters for #19: a component test should bind the intended state-machine contract, not
snapshot today's incomplete behavior. Coordinate with #14 rather than weakening the test to
make the current UI green.

## Dependency map for #11–#15

- #11 — still OPEN; current main nevertheless contains substantial real Soroban RPC/signing
  machinery and ScVal conversion helpers.
- #12 — still OPEN; current main contains a real wallet provider / wallet-kit path.
- #13 — still OPEN; current main contains live transaction/event projection machinery.
- #14 — still OPEN; current main contains action submission UI, but the legal transition
  matrix remains incomplete as described above.
- #15 — OPEN and GitHub-assigned to `Abdullah0x7`; current main contains event-stream related
  implementation.

Treat issue-state labels as incomplete capability signals; tests should follow exact current
interfaces plus the acceptance contracts of the dependency issues.

## Assigned implementation plan

1. **Keep the landed unit suite.**
   Extend, do not replace, the five-file Vitest foundation.

2. **Add component integration tests around the real UI seams.**
   At minimum cover:
   - PENDING / PROCESSING / terminal action availability according to #14;
   - failure-reason required / cancel / submit behavior;
   - pending action disables duplicate submissions;
   - missing contract configuration;
   - disconnected wallet -> connect path;
   - simulation/signing/network errors -> visible, stable error outcome;
   - successful write result -> success outcome without leaking signed payloads.

3. **Add a deterministic mocked-wallet / mocked-RPC Playwright harness.**
   Exercise:
   - connect wallet;
   - register a transaction fixture;
   - start processing;
   - complete it;
   - observe the updated status in the real UI.
   Use mocked/local/testnet fixtures only; never a funded wallet or mainnet funds.

4. **Add e2e CI on PRs.**
   Preserve current lint/typecheck/unit/build gates. Add browser installation/cache steps and
   `npm run test:e2e` in a deterministic job. A red e2e must fail the PR check.

5. **Enforce `lib/` coverage.**
   Add a Vitest coverage provider + explicit documented threshold. Include a deliberate
   below-threshold regression proving the gate actually fails.

6. **Add centralized RPC/signing error telemetry.**
   Use an optional environment-configured provider (Sentry or equivalent); no SDK should make
   local development fail closed. Test capture/no-capture behavior and sensitive-field
   scrubbing.

7. **Preserve repo-settings truth.**
   Making CI a *required* merge check is repository policy, not something a source PR alone can
   guarantee. The upstream connector reports no admin/push authority for this worker, so the
   assigned PR should document the exact required check name and request maintainer/admin
   branch-policy confirmation separately.

## Hostile test matrix

- illegal COMPLETE from PENDING is unavailable;
- START PROCESSING from terminal status is unavailable;
- FAIL requires a non-empty reason;
- repeated click during signing/submission does not duplicate a transaction;
- rejected wallet signature surfaces one stable UI error;
- simulated RPC error surfaces one stable UI error;
- send/poll failure does not present success;
- wallet-connect failure cannot fall through into signing;
- missing contract id stops before RPC;
- mocked lifecycle PENDING -> PROCESSING -> COMPLETED is observable end-to-end;
- e2e uses only mocked/local/testnet state;
- coverage below the configured threshold fails CI;
- telemetry-disabled local run remains functional;
- telemetry scrubs callback secrets and signed/XDR transaction material.

## Evidence limits

No fresh local `npm test` / Playwright execution is claimed by this research packet.
Runtime evidence cited from PR #79 remains historical author evidence. This packet is based on
exact GitHub connector readback of current main, issue/PR state, current source blobs, and
focused overlap searches.

## Fleet disposition

- Research lane: complete.
- Upstream implementation: not started.
- Upstream write authority observed: none (`push=false`).
- Current provider-side assignment/apply state: not independently verified by this seat.
- Open matching implementation carrier: none surfaced in focused searches.
- Recommended next: refresh GrantFox assignment state; if officially assigned, implement the
  residual test/e2e/coverage/telemetry scope above against a fresh main pin.
