# GrantFox baseline — Gryd-lock/grydlock-testkit #48

Operation: `GFOX-FRESH-GRYD-48-R1R2-ZZ-SOL-DRIFT`  
Worker: ZZ-Sol-Drift · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream testkit main: `7064404d6e7c44df1f980d532d23901651d79426`

## State

- GitHub: https://github.com/Gryd-lock/grydlock-testkit/issues/48
- GrantFox: https://contribute.grantfox.xyz/org/Gryd-lock/repo/grydlock-testkit/issue/48
- OPEN / Unassigned at observation
- 2 generic applicant comments
- implementation remains assignment-gated

## Current corpus

The issue's "11 destinations" premise is stale. Current `destinations.json` has **12** fixtures:

- clean: 4
- suspicious: 3
- malicious: 5
- entity types: 11 accounts + 1 asset
- fixture status: all `synthetic-only`

Pinned blobs:

- `destinations.json`: `c8f918089f700982d7347cc1d8af8d8677fd5774`
- `scores.json`: `f27d17c781a9059cc1304af93def554391b63876`
- `scripts/lib/taxonomy.mjs`: `44b6890344e7a01f108af0a9ced5f1caf7e945df`
- `scripts/validate-fixtures.mjs`: `90ea1abb6136d939aa4d1e0ac422b23f74fa6362`
- `scripts/__fixtures__/expected-counts.json`: `5ff39c9bf47388b81e0523ffd3365be167da30ab`
- README: `f8fd8bdb89cca82a0ef42735e3daf3b1cacfa895`
- CI: `53ce3ea3359798fd8b34d3a06728ae8110f7277c`

The shared taxonomy already defines 11 risk patterns:
`sweep`, `phishing-drainer`, `rug-pull`, `pass-through`, `scam-trustline`, `signer-takeover`, `memo-impersonation`, `sponsored-mule`, `cold-start`, `adversarial-clean`, `none`.

The current 12-fixture corpus only exercises:
`none`, `pass-through`, `scam-trustline`, `sweep`, `phishing-drainer`, `rug-pull`.

## Research constraint: no prevalence target exists

`Gryd-lock/grydlock-research` defines accuracy and false-positive rate as evaluation metrics, but its current README does **not** specify a real-world clean/suspicious/malicious base rate.

Therefore a 40/30/30 or similar fixture composition must be described as a **stratified evaluation corpus**, not as an estimate of production prevalence. Reporting raw overall accuracy from an intentionally balanced corpus as real-world expected accuracy would be misleading.

For false-positive measurement, clean sample size is especially important. With zero observed false positives, the approximate one-sided 95% upper bound is `1 - 0.05^(1/n)`:

- 40 clean fixtures → ~7.2%
- 60 clean fixtures → ~4.9%
- 100 clean fixtures → ~3.0%

So the example 40-clean target is weak if the intended claim is "FPR below 5%". A minimum of 60 clean negatives is a more defensible first milestone.

## Proposed first milestone: 120 fixtures

Treat 120 as an evaluation milestone, not a synthetic production prevalence claim:

- **60 clean**
- **30 suspicious**
- **30 malicious**

This keeps enough clean negatives to make a zero-FP result meaningfully constrain the FPR while retaining substantial positive/ambiguous coverage.

### Entity quotas

Use quotas as coverage floors, not exact prevalence:

| Entity | Clean | Suspicious | Malicious | Total |
| --- | ---: | ---: | ---: | ---: |
| account | 30 | 15 | 15 | 60 |
| asset | 10 | 5 | 5 | 20 |
| contract | 8 | 4 | 4 | 16 |
| muxed account | 5 | 3 | 2 | 10 |
| liquidity pool | 7 | 3 | 4 | 14 |
| **Total** | **60** | **30** | **30** | **120** |

If the fixture schema cannot yet represent one of these entity types, the tracker should mark that cell **blocked by schema issue**, not silently substitute more accounts.

### Risk-pattern floors

Every taxonomy value should have explicit coverage. Suggested first-pass floors:

- `none`: 45 clean
- `adversarial-clean`: 15 clean
- suspicious: at least 4 each for `pass-through`, `scam-trustline`, `signer-takeover`, `memo-impersonation`, `sponsored-mule`, `cold-start`; remaining 6 distributed to under-covered patterns
- malicious: at least 4 each for `sweep`, `phishing-drainer`, `rug-pull`, `signer-takeover`, `sponsored-mule`, `memo-impersonation`; remaining 6 distributed to under-covered patterns

These are **minimum coverage counts**, not a requirement that every entity × label × pattern cross-product be populated.

## Canonical tracker contract

After assignment, use one repository-owned canonical tracker:

`docs/FIXTURE_EXPANSION.md`

README Roadmap should link to it. The issue can remain the coordination thread, but should not be the only state store because PRs/releases need a versioned record.

The tracker should include:

1. pinned target milestone and rationale;
2. current label/entity/pattern counts;
3. explicit blocked schema cells;
4. checklist of sibling fixture issues/PRs with statuses;
5. a "last reconciled against destinations.json" commit;
6. a note that corpus mix is stratified evaluation, not production prevalence.

Do not manually maintain counts if CI can compute them.

## CI / validation contract

Extend the existing machine-readable `expected-counts.json` rather than creating a second independent taxonomy.

A safe staged design:

- keep current seed `mustExist` guarantees;
- add milestone target metadata and entity/pattern floors;
- teach `validate-fixtures.mjs` to calculate and print label/entity/pattern coverage;
- fail CI only on **regression below the achieved floor**, not because the repository has not instantly reached 120;
- once the 120 milestone is complete, raise minimums to the final committed target.

This prevents partial fixture PRs from being impossible to merge while still stopping backsliding.

## Assignment-time acceptance

1. publish `docs/FIXTURE_EXPANSION.md` and link it from README;
2. make current 12-entry reality the starting row, not the old 11-entry count;
3. encode 120 / 60-clean / 30-suspicious / 30-malicious as the first target milestone unless maintainers provide an evidence-backed alternative;
4. track all five entity classes and all 11 taxonomy patterns;
5. keep stats terminology honest: report per-label/class metrics and FPR separately from any prevalence-weighted metric;
6. add a deterministic coverage report/check that cannot drift from `destinations.json`.

## Application draft

> Applying for #48 after checking current `main@7064404d6e7c44df1f980d532d23901651d79426`. The corpus is now 12 fixtures, not 11 (4 clean / 3 suspicious / 5 malicious), while the shared taxonomy has already expanded to 11 risk-pattern values and most new patterns have zero destination coverage.
>
> I also checked `grydlock-research`: it defines accuracy and false-positive rate but does not document a real-world label base rate. I would therefore treat the expansion as a stratified evaluation corpus rather than claim a 40/30/30 mix represents production prevalence.
>
> My proposed first milestone is 120 fixtures with 60 clean / 30 suspicious / 30 malicious, explicit quotas across account/asset/contract/muxed/liquidity-pool entities, and coverage floors for every current taxonomy pattern. Sixty clean negatives are deliberate: even with zero observed false positives, 40 clean only constrains the one-sided 95% FPR upper bound to about 7.2%, while 60 gets it below 5%.
>
> Once assigned I would make `docs/FIXTURE_EXPANSION.md` the canonical versioned tracker linked from README, then extend the existing validation/count contract so partial PRs report progress and cannot regress achieved coverage without making the unfinished 120 target an immediate CI blocker.

## Authority boundary

This is a pre-assignment planning/source packet. No upstream source, provider assignment, reward, wallet, or payment state is changed.
