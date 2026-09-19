# GrantFox pre-assignment expansion plan — Gryd-lock/grydlock-testkit #48

Lane: `GFOX-FRESH-GRYD-48`  
Issue: https://github.com/Gryd-lock/grydlock-testkit/issues/48  
GrantFox: https://contribute.grantfox.xyz/org/Gryd-lock/repo/grydlock-testkit/issue/48

## Authority fence

This packet is research/data-design evidence only. It does not apply to GrantFox,
claim assignment, mutate upstream fixtures, submit an upstream PR, establish a
reward, or prove payment. The live provider page rendered **Unassigned** with an
Apply route and two generic applicant comments. A refreshed GitHub #48 PR census
returned no carrier after an earlier secondary-rate-limit hold cleared.

Upstream permissions from this seat are pull=true / push=false.

## Pinned source

Upstream `main`: `7064404d6e7c44df1f980d532d23901651d79426`

Exact blobs:
- `destinations.json`: `c8f918089f700982d7347cc1d8af8d8677fd5774`
- `README.md`: `f8fd8bdb89cca82a0ef42735e3daf3b1cacfa895`
- `scores.json`: `f27d17c781a9059cc1304af93def554391b63876`
- `package.json`: `7d674c82cfca132d720a02dbc56d45df3979e950`

## Current corpus: 12, not 11

The issue text still says 11 destinations. Current `main` has **12**:

| Label | Current | Share |
| --- | ---: | ---: |
| clean | 4 | 33.3% |
| suspicious | 3 | 25.0% |
| malicious | 5 | 41.7% |

Entity coverage is 11 classic `account` entries and one `asset`. There are no
muxed-account, contract, or liquidity-pool destinations yet.

Current primary `risk_pattern` counts:
- `none`: 4
- `pass-through`: 2
- `scam-trustline`: 1
- `sweep`: 1
- `phishing-drainer`: 1
- `rug-pull`: 3

Every current destination is explicitly `fixture_status: "synthetic-only"`.

## Base-rate finding

`grydlock-research` names accuracy and false-positive rate as evaluation
metrics, but its current README does **not** document a real-world clean /
suspicious / malicious prevalence. Therefore the issue's example 40/30/30 mix
must not be presented as an observed population base rate.

For Phase 1, use a deliberately stratified **evaluation/stress corpus**. If the
research/governance work later establishes a defensible population prevalence,
add a separate weighting/evaluation manifest rather than silently relabeling this
balanced fixture set as population-representative.

## Phase-1 target: 120 destinations

A 120-entry floor gives the fixture-expansion effort room for every planned
entity and risk family while staying reviewable. The target label mix is the
issue's proposed 40/30/30 evaluation split:

| Label | Target | Current | Delta |
| --- | ---: | ---: | ---: |
| clean | 48 | 4 | +44 |
| suspicious | 36 | 3 | +33 |
| malicious | 36 | 5 | +31 |
| **total** | **120** | **12** | **+108** |

This is a test-design allocation, not a fraud-prevalence estimate.

With 120 independent examples, a worst-case binomial proportion has roughly a
±9 percentage-point 95% margin before stratification. With 48 clean examples, a
zero-observed false-positive result still only implies an approximate
rule-of-three upper bound of 6.25%. So 120 is a useful Phase-1 regression/evidence
corpus, not enough to claim production-grade low-FPR precision.

## Entity/subtype quota

Treat muxed accounts as a tracked subtype even if the schema keeps
`type: "account"`.

| Entity/subtype | clean | suspicious | malicious | total | Current | Delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| classic account | 30 | 20 | 18 | 68 | 11 | +57 |
| muxed account | 6 | 3 | 3 | 12 | 0 | +12 |
| asset | 6 | 4 | 6 | 16 | 1 | +15 |
| contract | 3 | 4 | 5 | 12 | 0 | +12 |
| liquidity pool | 3 | 5 | 4 | 12 | 0 | +12 |
| **total** | **48** | **36** | **36** | **120** | **12** | **+108** |

The table forces every new entity family to contain positive controls and risky
cases, instead of making new types synonymous with "malicious".

## Canonical risk-pattern quota

Issue #50 already centralized the closed `risk_pattern` taxonomy. Reuse it;
do not invent a second tagging system.

| risk_pattern | clean | suspicious | malicious | target |
| --- | ---: | ---: | ---: | ---: |
| `none` | 30 | 0 | 0 | 30 |
| `adversarial-clean` | 12 | 0 | 0 | 12 |
| `cold-start` | 6 | 6 | 0 | 12 |
| `pass-through` | 0 | 6 | 3 | 9 |
| `sweep` | 0 | 3 | 6 | 9 |
| `phishing-drainer` | 0 | 3 | 6 | 9 |
| `rug-pull` | 0 | 4 | 7 | 11 |
| `scam-trustline` | 0 | 5 | 4 | 9 |
| `signer-takeover` | 0 | 4 | 4 | 8 |
| `memo-impersonation` | 0 | 3 | 3 | 6 |
| `sponsored-mule` | 0 | 2 | 3 | 5 |
| **total** | **48** | **36** | **36** | **120** |

The clean controls are intentionally concentrated in `none`,
`adversarial-clean`, and `cold-start`; structural lookalikes belong under the
existing `adversarial-clean` taxonomy rather than pretending a clean fixture
is itself a phishing/sweep/rug-pull case.

## Sibling issue dependency map

Destination-count contributors:
- #13 sponsored reserves → `sponsored-mule`
- #15 Soroban contracts → `contract` entity coverage
- #16 muxed accounts → muxed-account subtype
- #17 signer/threshold takeover → `signer-takeover`
- #18 liquidity-pool interactions → `liquidity_pool`
- #19 zero-history accounts → `cold-start`
- #21 memo scams → `memo-impersonation`
- #23 structurally malicious-looking clean controls → `adversarial-clean`
- #50 completed taxonomy → authoritative `risk_pattern` enum

Transaction-shape coverage that should be tracked by #48 but **must not inflate
the 120 destination denominator**:
- #14 fee-bump envelope
- #20 malformed/corrupt XDR
- #22 valid/expired time bounds

Tooling/governance dependencies:
- #63 machine-readable fixture summary is open and currently assigned; consume it
  if/when landed, but do not make #48 completion depend on a duplicate reporter.
- #73 synthetic-label/score governance is the natural place to decide provenance,
  reviewer agreement, calibration, and any later prevalence weighting.

## Canonical tracker recommendation

Use **issue #48 itself as the single human progress checklist**. The upstream
README roadmap should contain only:
1. the 120-entry headline target,
2. the 48/36/36 label split,
3. a direct link to #48 as the canonical tracker.

Do not maintain a second detailed checklist in README; two editable progress
ledgers will drift as many fixture PRs land.

Suggested checklist dimensions inside #48:
- [ ] label totals reach 48 / 36 / 36
- [ ] classic accounts 68
- [ ] muxed accounts 12
- [ ] assets 16
- [ ] contracts 12
- [ ] liquidity pools 12
- [ ] every canonical risk-pattern quota met
- [ ] #13 sponsored-reserve coverage
- [ ] #14 fee-bump transaction coverage
- [ ] #15 contract coverage
- [ ] #16 muxed-account coverage
- [ ] #17 signer-takeover coverage
- [ ] #18 liquidity-pool coverage
- [ ] #19 cold-start coverage
- [ ] #20 malformed-XDR coverage
- [ ] #21 memo coverage
- [ ] #22 time-bounds coverage
- [ ] #23 adversarial-clean controls
- [ ] README links to #48
- [ ] deterministic report/check validates target counts when reporting tooling is available
- [ ] downstream consumer contract test remains green before release

## Implementation sequencing after assignment

1. Re-pin upstream and refresh provider/carrier state.
2. Land/consume schema-capability prerequisites before adding entity types that
   validators cannot represent.
3. Grow in small auditable batches; every fixture PR states its contribution to
   the #48 quota matrix.
4. Require a matching score entry and validation for every destination.
5. Recompute the distribution after every batch; do not "catch up" labels at the
   end.
6. Keep transaction-only negative fixtures out of destination distribution math.
7. Before declaring #48 complete, run local validation plus the downstream
   consumer contract test and compare the final counts against the canonical
   target receipt.

## Residual questions for maintainers

- Confirm 120 as the Phase-1 floor versus exactly 100 or another size.
- Confirm that 40/30/30 is intentionally an evaluation split, not a claimed
  production prevalence.
- Confirm whether muxed accounts stay `type: account` plus subtype/address-form
  reporting or become an explicit type.
- Confirm whether future pattern additions extend the closed taxonomy by
  governance change or are mapped onto the existing enum.
- Confirm the eventual prevalence-weighted evaluation mechanism once #73 or
  grydlock-research documents a defensible base rate.
