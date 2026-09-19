# GrantFox baseline — Gryd-lock/grydlock-testkit #1

Operation: `GFOX-GRYDLOCK-1-R-CINDER17-20260919`  
Worker: ZZ-Cinder-17 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `7064404d6e7c44df1f980d532d23901651d79426`

## Canonical issue

- GitHub: https://github.com/Gryd-lock/grydlock-testkit/issues/1
- GrantFox: https://contribute.grantfox.xyz/org/Gryd-lock/repo/grydlock-testkit/issue/1
- Provider state observed: OPEN / Unassigned
- Existing issue comments: 2 assignment requests
- Upstream connector permission: read-only (`pull=true`, `push=false`)
- Reward labels are eligibility signals only; no fixed award or payment is asserted.

Issue #1 asks contributors to add Stellar StrKey validity checks for account addresses and asset issuers, using `@stellar/stellar-sdk`, while preserving aggregate error collection and the existing passing summary.

## Current-source finding

The defect still exists on current `main`.

`scripts/validate-fixtures.mjs` validates fixture status, labels, risk patterns, score coverage, golden counts, and original seed IDs. It does **not** validate:

- `type: "account"` → `address` as an Ed25519 public StrKey;
- `type: "asset"` → `asset_issuer` as an Ed25519 public StrKey;
- the account fixture's `id`/address relationship beyond score-key coverage.

Fresh repository code search found no `StrKey`, `@stellar/stellar-sdk`, or `isValidEd25519PublicKey` usage.

## Acceptance-text drift that matters

The issue says "All 11 existing destinations continue to pass." Current main contains **12** destinations and 12 scores: 11 account entries plus one asset entry. The validator itself calls the older set the "original 11 seed fixtures" while permitting the expanded corpus through golden minimums. An implementation should preserve **all 12 current fixtures**, not encode 11 as the total.

The issue also says there is no package-lock-tracked dependency. Current main now has `package-lock.json`, but its root package has **no dependencies**. `package.json` also has no `dependencies` field. Adding the SDK should update both package metadata and lockfile without disturbing existing scripts.

## Pinned evidence

- upstream `main`: `7064404d6e7c44df1f980d532d23901651d79426`
- `scripts/validate-fixtures.mjs`: `90ea1abb6136d939aa4d1e0ac422b23f74fa6362`
- `package.json`: `7d674c82cfca132d720a02dbc56d45df3979e950`
- `package-lock.json`: `c06528821738da20014efaec28eb20d4a61d8348`
- `.github/workflows/ci.yml`: `53ce3ea3359798fd8b34d3a06728ae8110f7277c`
- `destinations.json`: `c8f918089f700982d7347cc1d8af8d8677fd5774`
- `scores.json`: `f27d17c781a9059cc1304af93def554391b63876`

Current CI runs Node 20 and executes `npm run validate` after `secret-check`; no workflow change is needed for the intended dependency/validator patch.

## Assignment-gated implementation plan

After maintainer/provider assignment:

1. Add `@stellar/stellar-sdk` as a normal runtime dependency and update the existing npm lockfile.
2. Import `StrKey` into `scripts/validate-fixtures.mjs`.
3. For every account fixture, call `StrKey.isValidEd25519PublicKey(d.address)`; append a destination-specific error on false instead of throwing.
4. For every asset fixture, validate `d.asset_issuer` the same way and append a destination-specific error.
5. Preserve the current one-pass `errors` accumulator, nonzero exit on any error, and exact passing summary line.
6. Add focused regression coverage that corrupts one character of a known-good account and asset issuer and proves validation fails with the fixture ID in the diagnostic, while the untouched 12-fixture corpus stays green.
7. Run `npm ci`, `npm run validate`, `npm test`, and the same commands under Node 20 if the local default differs.

A good patch should not hand-roll CRC16, should not silently skip unknown fixture types, and should not freeze the stale total of 11.

## Provider application draft

> Applying for #1 after reading current `main@7064404d6e7c44df1f980d532d23901651d79426`. The reported integrity gap is still present: `scripts/validate-fixtures.mjs` checks taxonomy/score/golden-count rules but never calls Stellar StrKey validation. I also checked current-source drift before proposing the patch: the repo now has 12 fixtures (11 accounts + 1 asset), not the issue's older total of 11, and `package-lock.json` now exists but still tracks zero dependencies.
>
> After assignment I will add `@stellar/stellar-sdk`, validate every account `address` and asset `asset_issuer` with `StrKey.isValidEd25519PublicKey`, preserve aggregate error collection and the exact passing summary, and add corruption regressions for both account and issuer paths. I will verify all 12 current fixtures with `npm ci && npm run validate && npm test` on the repository's Node 20 CI contract.

## Authority / publication fence

No upstream implementation is started before official assignment. No fixture, issue state, provider assignment, wallet, payment, or reward state is changed by this baseline.
