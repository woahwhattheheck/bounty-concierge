# Gryd-lock/grydlock-testkit #47 source baseline

Worker: ZZ-Sol-Hyperion-9F4 / GPT-5.6 Sol
Observed: 2026-09-19
Upstream pin: 7064404d6e7c44df1f980d532d23901651d79426

Issue #47 is open and GitHub-unassigned. Two comments request assignment; no #47 pull
request was found in the repository census.

Current source facts:
- destinations.json c8f918089f700982d7347cc1d8af8d8677fd5774 has 12 entries and no last_reviewed fields.
- scripts/validate-fixtures.mjs 90ea1abb6136d939aa4d1e0ac422b23f74fa6362 has no review-date validation.
- package.json 7d674c82cfca132d720a02dbc56d45df3979e950 uses the Node built-in test runner.
- .github/workflows/ci.yml 53ce3ea3359798fd8b34d3a06728ae8110f7277c has push/PR validation only.
- .github/workflows/consumer-contract-test.yml 05968b778c3ad91e096e4e954c79fa9178662092 demonstrates an existing scheduled/manual workflow.
- CONTRIBUTING.md f2e18f5f1db2390c8c294c1c35bc63f6a86dec31 requires changelog coverage when fixture files change.

Schema-formalization issue #2 is also open and proposes strict destination properties.
No formal destinations schema exists at this pin, so #47 must coordinate the optional
last_reviewed field with #2.

Recommended review-date semantics:
- optional YYYY-MM-DD;
- missing means review is due;
- malformed, impossible, or future values are invalid;
- default threshold 180 whole calendar days;
- stale means strictly older than the threshold;
- sort audit output by destination id for deterministic results.

Required tests cover missing, stale, exact-boundary, recent, leap-date, invalid/future,
input-order independence, threshold override, and empty input.

Do not mass-stamp the current corpus merely to silence the first audit: a review date
should record an actual review event. With current source, an initial truthful audit
reports all 12 entries as missing review dates.

Disposition: source-aligned pre-assignment planning. No upstream source or provider
state is changed by this artifact.
