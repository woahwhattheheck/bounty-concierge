# GrantFox baseline — Gryd-lock/grydlock-testkit #1

Operation: `ZZF-GFOX-DISC-006-R-ZZ-SOL-DRIFT`  
Worker: ZZ-Sol-Drift · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `7064404d6e7c44df1f980d532d23901651d79426`

## State

- GitHub: https://github.com/Gryd-lock/grydlock-testkit/issues/1
- GrantFox: https://contribute.grantfox.xyz/org/Gryd-lock/repo/grydlock-testkit/issue/1
- OPEN / Unassigned at observation
- 2 existing applicant comments
- connector permissions: pull=true, push=false
- campaign labels do not establish a fixed reward or payment

## Current-source gap

The StrKey validation gap is real.

`scripts/validate-fixtures.mjs` validates fixture status, label, risk pattern, score presence/range, golden counts, and must-exist seeds, but it does not validate:

- `type: "account"` → `address`
- `type: "asset"` → `asset_issuer`

Pinned evidence:

- validator: `90ea1abb6136d939aa4d1e0ac422b23f74fa6362`
- destinations: `c8f918089f700982d7347cc1d8af8d8677fd5774`
- package.json: `7d674c82cfca132d720a02dbc56d45df3979e950`
- package-lock.json: `c06528821738da20014efaec28eb20d4a61d8348`
- CI workflow: `53ce3ea3359798fd8b34d3a06728ae8110f7277c`

## Spec drift that must be resolved

The issue text is stale in two material ways.

1. It says "all 11 existing destinations"; current `destinations.json` contains **12** entries: 11 accounts plus one asset fixture.
2. It says there is no package-lock-tracked dependency at all. Current main already has `package-lock.json`, though `package.json` still has no dependencies.

There is also a CI contradiction: the issue requires adding `@stellar/stellar-sdk` but says `.github/workflows/ci.yml` needs no change. Current CI runs `npm run validate` on a clean Node 20 runner **without `npm ci` or another dependency-install step**. Once the validator imports the SDK, the clean CI job cannot resolve that dependency unless installation is added somewhere.

Do not paper over this by hand-rolling CRC logic: the issue explicitly requires the SDK helper.

## Post-assignment implementation contract

After maintainer/provider assignment:

1. add `@stellar/stellar-sdk` to package + lock;
2. import `StrKey` and use `StrKey.isValidEd25519PublicKey`;
3. account fixtures: validate `address`;
4. asset fixtures: validate `asset_issuer`;
5. append descriptive errors into the existing `errors` array; do not throw or short-circuit;
6. preserve the exact passing summary line;
7. add a corruption regression that flips one character and asserts nonzero validation + offending destination identity;
8. reconcile CI dependency installation explicitly with maintainers. If a clean-run install step is required, change CI despite the stale "no workflow changes" sentence rather than shipping a workflow that cannot load the mandated SDK.

A useful negative matrix should cover checksum corruption, wrong version/type (e.g. non-G StrKey), truncation, and invalid asset issuer while confirming current valid fixtures remain green.

## Application draft

> Applying after checking current main at `7064404d6e7c44df1f980d532d23901651d79426`. The validation gap is still real: `validate-fixtures.mjs` never checks account `address` or asset `asset_issuer`, and I would use the required Stellar SDK StrKey helper while preserving the existing accumulated-error behavior and passing summary.
>
> Two acceptance details have drifted since the issue was written: current data has 12 destinations (11 account + 1 asset), and a package-lock now exists. More importantly, current CI runs `npm run validate` without `npm ci`; adding the required SDK import therefore needs a dependency-install seam on clean CI unless maintainers have another intended mechanism. I would resolve that explicitly rather than hand-roll checksum math or falsely claim no CI change is needed.
>
> Tests would corrupt a known-good key by one character, cover account and asset-issuer failures, and prove valid current fixtures still pass.

## Authority boundary

This packet is evidence/application preparation only. It does not change upstream source, assignment, provider state, wallet, reward, or payment.
