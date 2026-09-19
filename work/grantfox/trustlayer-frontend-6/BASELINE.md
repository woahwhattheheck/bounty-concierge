# GrantFox baseline — TrustLayer-Org/TrustLayer-Frontend #6

Operation: `GFOX2-20260919-113`  
Worker: ZZ-Solstice · GPT-5.6 Sol  
Observed: 2026-09-19

## Canonical issue

- GitHub: https://github.com/TrustLayer-Org/TrustLayer-Frontend/issues/6
- GrantFox: https://contribute.grantfox.xyz/org/TrustLayer-Org/repo/TrustLayer-Frontend/issue/6
- State at observation: OPEN / GitHub unassigned
- GrantFox state at observation: Unassigned; active application route
- Labels: `GrantFox OSS`, `Maybe Rewarded`, `Third Campaign`, `priority:medium`
- Existing issue comments/applications observed: 2
- Matching PR carrier surfaced: no
- Reward posture: eligibility signal only; no fixed award or payment asserted
- Contributor rule: wait for maintainer assignment before implementation

Pinned source generations:
- frontend main: `9015d666984f1ae29657b6dfe2de3495c646b325`
- backend main: `6ba347951ab6e99e46c4983b2e15f370f214e15f`
- contracts main: `aa7ec24afc58273d333272ee7068a61ba9bf4336`

## Current-source finding

The frontend's score-band semantics are not expressed by one versioned policy.

`src/lib/trust.js` defines `TIER_THRESHOLDS` at 0/20/40/60/80, but those same boundaries are separately encoded in:
- `scoreToLabel`
- `scoreToColorClass`
- `scoreToBarClass`
- `scoreGrade`
- `scoreToTextClass`
- next-tier/tier-index helpers

`src/components/ScoreCard.js` adds an independent `TIER_COPY` object keyed by labels, while `src/components/ScoreLegend.js` renders only from `TIER_THRESHOLDS`. A single threshold/label change can therefore drift helper behavior, visual treatment, grade, explanatory copy, legend, and next-tier guidance inside the frontend itself.

Pinned frontend blobs:
- `src/lib/trust.js`: `3732855309c1b8cbc374b33132bb27a8cb5029a8`
- `src/components/ScoreCard.js`: `b76608d54c704aeccd18a5fa2e3189335559dafe`
- `src/components/ScoreLegend.js`: `0c262949bb5425ee9fe2b7d8bab781421b2b7b77`
- `package.json`: `76402a8f3f30688c98233b4527e0e8f28cc3d656`

Cross-repository authority check matters before implementation:
- backend README blob `2a274240b90206e56f56cc1bc8165ddc10da14e1` documents versioned v2 score/provenance endpoints and a calculation version, but does not document the 0/20/40/60/80 display-band policy as authoritative.
- contracts README blob `722035fde01ffd31daa4994d0ae4c17c2c81332c` documents a separate verification-tier field bounded to `0..=10`. That is not automatically the same semantic object as the frontend's 0..100 score bands.

A correct implementation must therefore establish the intended policy authority rather than silently mapping the contract's 0..10 verification tier onto the frontend score bands.

## Validation-gap finding

Current `npm test` runs:
- `npm run lint`
- `node --test tests/history.test.js`

Vitest is present in devDependencies but score-boundary/parity tests are not wired into the current `npm test` command by the inspected package manifest. The implementation must add an explicit executable boundary-vector/drift gate and ensure CI actually invokes it.

## Narrow implementation plan after assignment

1. **Establish authority first**
   - Maintainer chooses either a versioned API contract or a generated/checksummed shared fixture as the canonical score-band policy.
   - Keep the contract's 0..10 verification-tier semantics separate unless the maintainers explicitly bind the two concepts.

2. **One policy object**
   - Store version plus every display semantic needed by the frontend: score minimum/boundary, label, grade, color classes/tokens, explanatory copy, and provenance.
   - Derive label, grade, color, legend, next-tier gap/label, and card copy from the policy rather than repeated numeric branches.

3. **Fail closed on policy drift**
   - Missing, malformed, or unknown future policy versions render a clear unsupported/unavailable state instead of silently applying stale local thresholds.
   - Do not persist sensitive or unnecessary API payloads in browser storage.

4. **Parity fixtures + CI**
   - Add exact vectors immediately below/at/above every boundary.
   - Run the same vectors against the authoritative producer/fixture and frontend mapper.
   - Add an explicit CI command that detects a changed upstream policy without regenerated/reviewed fixtures.

## Source-specific application draft

> Applying for #6 after checking current frontend, backend, and contracts source rather than assuming the issue's word “tier” refers to one object. Frontend `src/lib/trust.js` still duplicates 0/20/40/60/80 thresholds across label/color/bar/grade/text helpers, `ScoreCard.js` owns separate tier copy, and `ScoreLegend.js` reads only `TIER_THRESHOLDS`. The backend documents versioned score/provenance endpoints, while the contract's documented verification tier is a separate 0..=10 field, so I would first establish the actual policy authority instead of conflating those concepts.
>
> Approach:
> 1. Define/bind one versioned authoritative score-band policy (API schema or generated fixture) carrying thresholds, labels, grades, visual tokens/copy and provenance.
> 2. Refactor every frontend score-band helper and UI surface to derive from that policy; unknown/malformed/future versions fail to an unsupported/unavailable state rather than stale labels.
> 3. Add shared vectors immediately below/at/above every threshold and a CI drift gate against the authoritative source.
> 4. Wire the new parity suite into the repository's real test/CI path; current `npm test` only runs ESLint plus `tests/history.test.js`.
>
> I will wait for maintainer assignment before implementation, per the issue requirements.

## Authority fence

This packet is pre-assignment evidence only. It does not modify upstream source, provider assignment, reward status, payment state, or account settings. Upstream repositories are readable but not pushable through the current GitHub installation.
