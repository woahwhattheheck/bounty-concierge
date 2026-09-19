# GrantFox baseline — Arbitra-Lab/Arbitra #3

Operation: `GFOX3-20260919-arbitra-3-R-ZZ-SOL-DELTA`  
Worker: ZZ-Sol-Delta · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `b98c3f7eb6ee283c97ef7bfd8917280a8197b698`

## Canonical issue/provider state

- GitHub issue: https://github.com/Arbitra-Lab/Arbitra/issues/3
- GrantFox: https://contribute.grantfox.xyz/org/Arbitra-Lab/repo/Arbitra/issue/3
- Issue: OPEN; assignee: none; 1 existing application comment
- Live GrantFox page: Unassigned; `Apply to this issue` visible; one application per user; direct GitHub comment
- Open PR census at observation: none
- Connector permission snapshot: pull=true, push=false
- Labels include `critical`, `frontend`, `tooling`, `ci-cd`, `Maybe Rewarded`, `GrantFox OSS`, and `Official Campaign | FWC26`
- Reward interpretation: campaign / `Maybe Rewarded` metadata is not a fixed award or payment guarantee.

This packet is pre-assignment evidence only. It does not mutate upstream source or claim provider assignment.

## Exact current failure contract

At the pinned main SHA, ESLint is not merely misconfigured; every enforcement path is intentionally neutralized.

### Empty flat config

`frontend/eslint.config.mjs` is exactly:

```js
export default [];
```

Pinned blob: `d6d1738de67ec12cd1cae1bbef0525681e0f82c7`.

### Lint scripts are explicit no-ops

`frontend/package.json` blob `827361835145b256b32bab942a3f321583d931ba` defines:

- `lint`: an `echo` saying ESLint is disabled because of package compatibility
- `lint:fix`: another `echo` no-op
- `check`: `pnpm run lint && pnpm run format:check && pnpm run build`

So `check` can report success without running ESLint.

Pinned frontend versions:

- Next: `16.2.6`
- React / ReactDOM: `19.2.3`
- ESLint: `^9.0.0`
- `eslint-config-next`: `16.1.3`
- `@eslint/eslintrc`: `2.1.4`
- TypeScript: `^5`

The Next runtime and Next ESLint config are on different 16.x minors. That mismatch should be reconciled as part of the repair, but the current source alone does not prove it is the only historical incompatibility.

## CI currently overstates lint coverage

`.github/workflows/frontend-ci-cd.yml` blob `98ba9e380a8c112cd11b424d479ab9144653da0b` names its job `Lint, Format & Test` and has a `Run ESLint` step executing `pnpm run lint` from `frontend/`.

Because the package script is an echo no-op, the workflow currently records a successful “ESLint” step without linting source.

`frontend/check-all.sh` blob `abdf538be41432a870128ebde5c778a36b1eaec0` likewise runs formatting, `pnpm run lint`, then build, so its lint stage is also a no-op.

The frontend README advertises `make lint`, `make ci`, and a CI/local parity story; the literal scripts currently do not provide that lint guarantee.

## Pre-commit infrastructure exists but does not enforce ESLint from the root hook

Root `.husky/pre-commit` blob `c37466e2b308ae097b510ac4f3c8b983ae0dbab7` runs:

```sh
npx lint-staged
```

Root `package.json` blob `632c4289e94bf68d4481bc2185f31d7ce3358723` configures root lint-staged JavaScript/TypeScript handling as **Prettier only**.

The frontend package separately declares a lint-staged rule for `eslint --fix`, but the repository-level hook is launched at the root and uses the root package's lint-staged configuration. Therefore the existing pre-commit hook infrastructure does not satisfy issue #3's “pre-commit hooks for linting” acceptance criterion.

A repair should have one authoritative hook/config path and a regression proving staged frontend TS/TSX files actually execute ESLint.

## Acceptance scope exists on the pinned tree

The issue's requested code areas are present:

- `frontend/app/admin/`
- `frontend/app/user/`
- `frontend/components/`

The component tree also contains `components/blockchain/` and `components/stellar/`, providing concrete locations for any project-specific blockchain/SDK rules rather than applying vague global restrictions.

## Existing applicant is generic, not source-specific

The sole existing issue comment proposes implementation/UI/accessibility/component testing but does not identify the empty ESLint config, no-op scripts, CI false-green seam, root-vs-frontend lint-staged split, or exact Next/ESLint package versions.

## Narrow post-assignment repair plan

1. Align `eslint-config-next` with the pinned Next 16 line and keep ESLint 9 flat-config semantics explicit.
2. Replace the empty `frontend/eslint.config.mjs` with a real flat config using the supported Next/React/TypeScript presets rather than legacy `.eslintrc` compatibility shims unless a pinned package truly requires them.
3. Restore `lint` and `lint:fix` as real ESLint commands over `app/admin`, `app/user`, `components`, and shared code those paths import.
4. Encode blockchain-specific rules narrowly around the actual Stellar/blockchain code surface. Prefer concrete safety/quality rules over broad rules that generate unrelated churn.
5. Make the root pre-commit route execute ESLint for staged frontend JS/TS files. Remove or reconcile the competing frontend-local lint-staged policy so there is one observable authority.
6. Keep Prettier ordering deterministic with ESLint autofix; verify staged-file behavior from repository root, not only from `frontend/`.
7. Keep the existing CI `pnpm run lint` step, but make it real and fail on lint findings.
8. Add a regression/smoke proof that the lint command visits representative admin, user, component, Stellar/blockchain, and intentionally-bad fixture/source cases.
9. Update README/Makefile language only after the lint command is genuinely enforced.

## Verification matrix after assignment

A correct repair should record exact commands and exit codes for at least:

- clean install using the pinned lockfile;
- `pnpm run lint`;
- `pnpm run lint:fix` on a disposable fixture/worktree case;
- `pnpm run format:check`;
- frontend unit tests;
- production build;
- root-level staged-file hook simulation with a TS/TSX lint violation proving ESLint is invoked;
- a corrected staged file proving hook success;
- direct lint coverage over representative `app/admin`, `app/user`, `components/stellar` or `components/blockchain` code.

Do not treat `next build` as a substitute for ESLint: Next 16 no longer supplies the old implicit lint safety net that this repository's scripts currently imitate with a message.

## Application draft

> I checked current `main@b98c3f7eb6ee283c97ef7bfd8917280a8197b698` before applying. The immediate failure is source-visible: `frontend/eslint.config.mjs` is literally `export default [];`, and both `lint` and `lint:fix` are echo no-ops. That also makes the existing GitHub Actions step named “Run ESLint” a false-green path because it just calls the disabled script. The repository already has Husky, but its root lint-staged config only runs Prettier; the frontend-local lint-staged rule that mentions ESLint is not the authority used by the root hook.
>
> After assignment I would align the Next 16 / ESLint package line, replace the empty flat config with supported Next/React/TypeScript config, restore real lint/lint:fix commands across admin, user and components, add narrowly scoped rules for the Stellar/blockchain surface, and make the root pre-commit path actually execute ESLint on staged frontend files. I would verify the real CI command, staged-file failure/success cases, tests and production build, and avoid claiming build success as lint coverage.
>
> I will wait for official assignment before assignment-dependent upstream changes.

No provider submission is claimed in this packet. A separate provider browser run for another already-claimed issue was still nonterminal while this packet was assembled, so this application text is preserved durably rather than starting a second ambiguous browser session.

## Authority boundary

No upstream source, issue assignment, provider state, wallet, award, or payment is changed by this packet. The owned `woahwhattheheck/bounty-concierge` repository is used only to preserve the source census and application-ready plan.
