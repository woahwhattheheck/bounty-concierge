# GrantFox baseline — Gryd-lock/grydlock-testkit #73

Operation: `GFOX-FRESH-GRYD-73/R1-governance-source-audit`  
Worker: ZZ-Sol-Forge · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream: `Gryd-lock/grydlock-testkit`  
Pinned upstream head: `7064404d6e7c44df1f980d532d23901651d79426`  
Issue: https://github.com/Gryd-lock/grydlock-testkit/issues/73

## Provider / authority fence

At observation time the GitHub issue is OPEN with no assignee and one generic "please assign" comment. The live GrantFox census published to the swarm reported **Unassigned**, one prior generic applicant, and no development carrier. This connector has pull but not push authority on the upstream repository.

This package is a current-source governance audit and assignment-ready design. It does **not** apply for the bounty, claim provider assignment or reward, or mutate upstream source.

## What the current repository actually does

The current label/score assignment path is human-authored data plus prose review:

1. `destinations.json` stores each synthetic fixture's `label`, `risk_pattern`, `notes`, and `fixture_status`.
2. `scores.json` independently maps the same fixture id to a static integer 0–100 score.
3. `CONTRIBUTING.md` tells authors to infer labels from a prose rubric and assign a score inside a label-associated range:
   - clean: 0–25
   - suspicious: 40–70
   - malicious: 75–100
4. The reviewer checklist asks a reviewer to decide whether the one-sentence notes make the label "defensible", whether the score is in the documented band, and whether scores vary inside a band.
5. `scripts/validate-fixtures.mjs` enforces schema/taxonomy correspondence, score numeric range 0–100, golden minimum counts, and required seed ids. It does **not** enforce label↔score band consistency, provenance, reviewer identity/agreement, evidence sufficiency, calibration version, or exception authority.
6. `.github/workflows/ci.yml` runs validation and, for fixture diffs, checks only that `CHANGELOG.md [Unreleased]` contains non-comment text. A prose changelog entry is therefore an audit hint, not a structured authorization record.

Pinned evidence blobs:
- `destinations.json`: `c8f918089f700982d7347cc1d8af8d8677fd5774`
- `scores.json`: `f27d17c781a9059cc1304af93def554391b63876`
- `CONTRIBUTING.md`: `f2e18f5f1db2390c8c294c1c35bc63f6a86dec31`
- `scripts/validate-fixtures.mjs`: `90ea1abb6136d939aa4d1e0ac422b23f74fa6362`
- `.github/workflows/ci.yml`: `53ce3ea3359798fd8b34d3a06728ae8110f7277c`

## Current dataset shape

There are 12 synthetic-only fixtures:
- clean: 4, scores 2 / 3 / 4 / 6
- suspicious: 3, scores 55 / 58 / 62
- malicious: 5, scores 85 / 89 / 92 / 95 / 97

The present data happens to cluster far from most warning-tier boundaries. That masks a policy inconsistency in the written rules.

## Cross-repo semantic hazard

`Gryd-lock/grydlock-research@24bab091f5ec9dc98af1a69bacaeb547b15d980b` defines the product warning tiers as:

- 0–20: Low
- 21–50: Elevated
- 51–75: High
- 76–100: Critical

Its README blob is `c20c29f7cb90dc4a20a74ca8d1964e64d6bff2aa`.

Those product thresholds do not align with the testkit's documented label score bands. Under the current contributor policy, a **clean** fixture may legally receive 21–25 and therefore map to **Elevated**; a **suspicious** fixture may map to either Elevated or High; and a **malicious** fixture at exactly 75 maps to High rather than Critical.

This is not proof that labels and warning tiers must be one-to-one. They may intentionally represent different concepts. The defect is that the current governance text does not state such a distinction, justify the overlapping boundaries, or require evidence explaining cross-boundary assignments. Because the research plan measures tier accuracy against labelled fixtures, leaving that relationship implicit makes future evaluation vulnerable to circular or contradictory ground truth.

## Existing downstream provenance work to reuse

`Gryd-lock/grydlock-oracle-adapter@81b09e371694998d5c42e66be656aaaef409c176` already introduced an evidence-bearing `RiskDecision` contract and a privacy-safe `RiskDecisionProvenance` design. Relevant docs:
- `MIGRATION_NUMERIC_TO_EVIDENCE.md` blob `4df34e6d3a1043b56b356ba0a12090e6038cddca`
- `PRIVACY_PROVENANCE.md` blob `7b578e52414f83232249bcc73955537830a1ec1c`

The #73 solution should align conceptually with that direction: preserve the difference between raw evidence, a risk judgment, downstream policy, and degraded/unknown states. It should not invent a second incompatible provenance vocabulary.

## Disposition

**READY_FOR_ASSIGNMENT_NOT_IMPLEMENTATION.**

The implementation target after provider/maintainer assignment is not "pick better numbers." It is to make label and score decisions auditable, independently reviewable, versioned, privacy-safe, and machine-checkable while preserving deterministic fixtures. The concrete proposal is in `GOVERNANCE.md`.
