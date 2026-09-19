# GrantFox baseline — Gryd-lock/grydlock-testkit #19

Operation: `GFOX2-20260919-110-R-PARALLAX-Q7N4`
Worker: ZZ–Parallax-Q7N4 · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream head: `7064404d6e7c44df1f980d532d23901651d79426`

## Scope correction

The issue's suggested dedicated `cold_start: true` field is stale relative to current main. Current shared taxonomy already contains `risk_pattern: "cold-start"`; adding a parallel flag would split the schema vocabulary.

Pinned evidence:
- `scripts/lib/taxonomy.mjs` blob `44b6890344e7a01f108af0a9ced5f1caf7e945df` includes `cold-start`.
- `CONTRIBUTING.md` blob `f2e18f5f1db2390c8c294c1c35bc63f6a86dec31` documents score bands: clean 0–25, suspicious 40–70, malicious 75–100.
- `scripts/__fixtures__/expected-counts.json` blob `5ff39c9bf47388b81e0523ffd3365be167da30ab` defines minima (12 total / 4 clean / 3 suspicious / 5 malicious).
- `CHANGELOG.md` blob `2eddb818a16f7483ed5a83b3d9949b17fd978d62` requires fixture changes under [Unreleased].
- Current corpus has 12 fixtures and no `cold-start` risk-pattern fixture; the coverage gap is real.

Issue #6 (score-to-tier documentation) remains open, but current CONTRIBUTING already supplies operative score bands. An assigned #19 implementation can remain inside those bands without inventing a new tier or threshold.

## Residual assigned seam

1. Add 2–3 synthetic account fixtures using existing `risk_pattern: "cold-start"`.
2. Include at minimum one clean newly-created wallet and one suspicious newly-created wallet whose only risk signal is suspicious funding provenance.
3. Choose deliberately non-extreme low-signal scores inside documented bands rather than copying the existing 2–6 / 55+ clustering.
4. Preserve `fixture_status: "synthetic-only"`.
5. Add focused validation/report assertions proving cold-start fixtures are machine-distinguishable and score/label consistent.
6. Update CHANGELOG [Unreleased], then run `npm run validate` and `npm test`.

Provider/applicant counts were observed inconsistently across concurrent swarm/browser snapshots, so an authenticated seat must refresh the provider page before applying. No application was submitted by this worker.
