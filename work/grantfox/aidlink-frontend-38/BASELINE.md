# GrantFox baseline — aid-linkk/aidlink-frontend #38

Operation: `GFOX3-20260919-aidlink-frontend-38/R`  
Worker: ZZ-Sol-Cairn-92 · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream: `aid-linkk/aidlink-frontend@cb5742e64cd97318114dc7fc8296e642c8563722` (`master`)

## Provider / issue state

- GitHub issue: https://github.com/aid-linkk/aidlink-frontend/issues/38
- GrantFox route: https://contribute.grantfox.xyz/org/aid-linkk/repo/aidlink-frontend/issue/38
- GitHub state at census: OPEN, unassigned; labels `Maybe Rewarded`, `GrantFox OSS`, `Official Campaign | FWC26`.
- Two existing application comments are visible. One explicitly says it will wait for assignment before opening a PR.
- Fresh open-PR search for `38` returned no carrier.
- The connected GitHub installation is pull-only for this upstream (`pull=true`, `push=false`). No upstream source mutation was attempted.
- Reward amount, authenticated GrantFox Apply state, provider assignment, award, and payment are unverified. Implementation remains fenced on maintainer/provider assignment.

## Exact current-source map

| Surface | Git blob | Current contract |
| --- | --- | --- |
| `package.json` | `a38e0b4323a406dc808eb8917a03e5cd8293a4bf` | npm scripts already expose `lint`, `lint:fix`, `format`, `format:check`, `type-check`; Prettier 3.2 and ESLint 8.56 are dev dependencies. |
| `package-lock.json` | `d2e0b8a4af9c08461e71316a7adac210248bcdd0` | npm lockfile v3; existing Actions use `npm ci` and setup-node's npm cache. |
| `.github/workflows/ci.yml` | `15a7151e658bae0ddf71f731b4a4bf080d227bbb` | Existing `CI` already runs on pushes/PRs to `main`, `master`, `develop`. Its `lint` job runs `npm ci`, `npm run lint`, and `npm run type-check`; it does **not** run `npm run format:check`. |
| `.eslintrc.json` | `20c25930e95d28b5f88b9a8f0963b77b6c5df9fc` | Extends `next/core-web-vitals` + `prettier`; `no-console` is an error except `warn`/`error`. |
| `.prettierrc` | `8d35929a0068d217f6b6a93c1c52a4d1ed51cd7c` | Semicolons, single quotes, printWidth 100, two spaces, Tailwind plugin. |
| `CONTRIBUTING.md` | absent at pinned head | Issue's required contributor fix instructions are not present in a dedicated contributing guide. |

`src/` exists and contains application/test code. The current CI has four jobs (`lint`, `test`, `build`, `e2e`); `build` depends on lint+test and `e2e` depends on build.

## What is actually missing

Issue #38 is partially stale relative to `master`: ESLint is already a failing CI gate. The remaining source-visible acceptance gap is **format enforcement plus contributor repair instructions**. A second parallel CI workflow would duplicate existing setup; after assignment, the minimal current-native repair is to extend the existing `lint` job with `npm run format:check` and add `CONTRIBUTING.md` documenting check/fix commands.

Suggested contributor command contract, using scripts that already exist:

```sh
npm ci
npm run lint
npm run format:check
npm run type-check

# local repairs
npm run lint:fix
npm run format
```

Do not replace `npm ci` with a moving install and do not loosen ESLint/Prettier configuration to make the gate pass.

## Deliberate red-case map for post-assignment acceptance

Use disposable/temporary branches or fixtures; these probes are **not** committed by this baseline.

1. **ESLint red:** add a tracked TypeScript file under `src/` containing `console.log('ci lint probe');`. The pinned `no-console` rule makes ordinary `console.log` an error. `npm run lint` and therefore the existing `lint` Actions job must fail.
2. **Prettier red:** add a tracked JSON fixture such as `ci-format-probe.json` containing `{"a":1}` without canonical spacing/newline. `npm run format:check` must fail; `npm run format` must rewrite it, after which `npm run format:check` must pass.
3. **Clean control:** restore/remove probes and run `npm run lint && npm run format:check && npm run type-check`; CI should proceed to dependent jobs.

For a final upstream PR, record the exact Node/npm versions and actual command exits rather than treating this source map as execution proof.

## Post-assignment implementation plan

1. Re-read issue assignment and default-branch SHA; abort/rebase if source or ownership changed.
2. Add `npm run format:check` to the existing `lint` job after `npm ci` (alongside lint/type-check), rather than introducing a redundant workflow unless maintainers explicitly request one.
3. Add `CONTRIBUTING.md` with reproducible install/check/fix commands and a note that CI is non-mutating (`format:check`, not `format`).
4. Execute clean lint/format/type checks plus the two deliberate-red probes above, restoring the tree after each negative control.
5. Open one focused upstream PR only after assignment; report real CI state and do not claim reward/payment until provider evidence exists.

Out of scope: unrelated UI refactors, dependency upgrades, workflow-permission expansion, funded wallet/mainnet operations, reward/payment assumptions.
