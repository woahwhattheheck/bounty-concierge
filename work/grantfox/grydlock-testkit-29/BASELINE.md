# GrantFox current-source baseline — Gryd-lock/grydlock-testkit #29

Operation: `GFOX2-20260919-061-R-ZZ-SOLSTICE`  
Worker: ZZ-Solstice · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `7064404d6e7c44df1f980d532d23901651d79426`

## Provider and issue state

- GitHub issue: https://github.com/Gryd-lock/grydlock-testkit/issues/29
- GrantFox listing: https://contribute.grantfox.xyz/org/Gryd-lock/repo/grydlock-testkit/issue/29
- GitHub state observed: OPEN / no assignee.
- GrantFox state observed via live listing: **Unassigned**, with **Apply to this issue** available.
- The listing says one application per user and publishes the application as a direct GitHub comment.
- One prior contributor application comment was present when observed.
- Reward labels are eligibility signals only; no fixed award, adjudication, or payment is asserted.

GrantFox's contributor guide says an accepted contributor is officially assigned and that assignment is the signal to start work. This packet therefore stops before assignment-dependent upstream implementation.

## Exact current-source gap

At pinned upstream main:

- `package.json` blob `7d674c82cfca132d720a02dbc56d45df3979e950` has no `engines` field.
- `.github/workflows/ci.yml` blob `53ce3ea3359798fd8b34d3a06728ae8110f7277c` hard-codes Node 20 in both the secret-check and validation paths rather than exercising a compatibility matrix.
- `scripts/validate-fixtures.mjs` blob `90ea1abb6136d939aa4d1e0ac422b23f74fa6362` uses standard ESM plus `node:fs`, `node:url`, `import.meta.url`, `Set`, `Object.entries`, and `Object.hasOwn`. Nothing in this inspected validator requires a floor above Node 18.
- The package's `validate` command runs both `validate-fixtures.mjs` and `validate-scenarios.mjs`; a correct CI matrix must exercise the existing command rather than only one validator.

No matching open implementation PR was found during the baseline pass.

## Proposed bounded implementation after assignment

1. Re-read literal current main and any newly linked PRs immediately before implementation.
2. Establish the floor from the complete runtime/API surface used by the package, not from guesswork; the inspected fixture validator is compatible with the issue's proposed Node 18 floor.
3. Add an explicit `engines.node` contract matching the proven floor.
4. Convert the validation path to a Node 18/20/22 matrix while preserving existing secret checks and any subsequently landed CI steps.
5. Exercise the repository's existing `npm run validate` command on every supported runtime.
6. Decide/document whether below-floor installs warn or fail; do not silently add `engine-strict` without explaining the contributor impact.
7. Pin the resulting upstream head and report exact matrix/test results.

## Prepared application text

> I’d like to take this on. I inspected current main at 7064404d6e7c44df1f980d532d23901651d79426: package.json still has no engines field, while .github/workflows/ci.yml pins Node 20 for secret-check and validate. My approach is to establish the actual minimum from the repository’s current ESM/Node API surface, declare and document that floor, convert the validation path to a Node 18/20/22 matrix without dropping existing checks, and verify the configured npm behavior below the declared floor. I’ll keep the change scoped to runtime compatibility/CI, preserve the existing validation and secret checks, and report exact matrix/test results. Please assign me through GrantFox if this approach fits.

The application is **prepared, not claimed submitted** in this baseline. Provider submission must be recorded only after a concrete provider/GitHub receipt exists.

## Authority

This is evidence and planning only. It grants no upstream implementation, maintainer assignment, provider adjudication, wallet, reward, payment, or revenue authority.
