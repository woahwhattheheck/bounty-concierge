# GrantFox authority baseline — TrustLayer-Org/TrustLayer-Frontend #6

Operation: `GFOX2-20260919-113/R-authority-map+provider-application`  
Worker: ZZ-Kepler-Sol · GPT-5.6 Sol  
Observed: 2026-09-19

## Pinned repository state

| Surface | Head |
|---|---|
| Frontend | `9015d666984f1ae29657b6dfe2de3495c646b325` |
| Backend | `6ba347951ab6e99e46c4983b2e15f370f214e15f` |
| Contracts | `aa7ec24afc58273d333272ee7068a61ba9bf4336` |

Issue: https://github.com/TrustLayer-Org/TrustLayer-Frontend/issues/6  
GrantFox: https://contribute.grantfox.xyz/org/TrustLayer-Org/repo/TrustLayer-Frontend/issue/6

At census: issue OPEN, GitHub-unassigned, two existing application comments, no #6 PR carrier surfaced. Labels include `GrantFox OSS`, `Maybe Rewarded`, `Third Campaign`, `priority:medium`. The issue explicitly requires maintainer assignment before implementation. Reward amount/award/payment are not inferred.

## Frontend drift proof

`src/lib/trust.js` blob `3732855309c1b8cbc374b33132bb27a8cb5029a8`:

- `TIER_THRESHOLDS` defines display bands at 0, 20, 40, 60, 80 with labels Untrusted / Low / Moderate / High / Excellent.
- `scoreToLabel` independently re-encodes 20/40/60/80.
- `scoreToColorClass`, `scoreToBarClass`, `scoreToTextClass`, and `scoreGrade` independently re-encode those boundaries.
- Next-tier helpers consume `TIER_THRESHOLDS`, so changing only one family can create internally inconsistent UI semantics.

`src/components/ScoreCard.js` blob `b76608d54c704aeccd18a5fa2e3189335559dafe`:

- Adds a separate `TIER_COPY` object keyed by the tier label.
- Already renders backend provenance including `result.calculationVersion`, which provides a natural place to surface display-policy provenance/version.

`src/components/ScoreLegend.js` blob `0c262949bb5425ee9fe2b7d8bab781421b2b7b77`:

- Renders directly from `TIER_THRESHOLDS`, another consumer that can drift from the independently hard-coded helpers.

`package.json` blob `76402a8f3f30688c98233b4527e0e8f28cc3d656`:

- `npm test` runs ESLint and only `tests/history.test.js`.
- Vitest is present as a dev dependency, but score-policy boundary/parity coverage is not part of the default test command today.

## Cross-repository authority distinction

Backend README blob `2a274240b90206e56f56cc1bc8165ddc10da14e1` documents:

- versioned v2 score/verification contracts;
- source provenance;
- calculation version;
- score range 0..100.

It does **not** document an authoritative frontend display-tier policy containing threshold/label/grade/color/copy semantics.

Contracts README blob `722035fde01ffd31daa4994d0ae4c17c2c81332c` documents a separate **verification tier** business-profile field bounded to `0..=10`.

That 0..10 contract field must not be silently treated as the authority for the frontend's 0..100 display bands. They are distinct concepts unless maintainers intentionally bind them in a documented compatibility contract.

## Assignment-gated design

1. Establish one authoritative, versioned **display score policy** from the API schema or a generated/checksummed fixture. It should carry threshold, label, grade, styling tokens, explanatory copy, next-tier semantics, and provenance/version.
2. Make every frontend helper and UI surface consume that object rather than encode numeric boundaries or label-keyed copy independently.
3. Validate policy shape/version before rendering. Missing, malformed, or unsupported future versions fail closed to an explicit unavailable/unsupported state rather than silently falling back to stale labels.
4. Add exact boundary vectors covering each lower edge, just-below edge, top end, and invalid response. Reuse the vectors on the authoritative producer and frontend consumer where feasible.
5. Wire the parity/drift check into the repository's default CI/test path; do not add a test command that hosted CI never calls.
6. Surface policy version/provenance next to the existing calculation-version provenance where appropriate.
7. Preserve the separate contract `0..=10` verification-tier semantics unless an explicit cross-repo design changes them.

## Application draft

> Applying for #6 after tracing the current frontend, backend and contract surfaces.
>
> Current frontend `main` is `9015d666984f1ae29657b6dfe2de3495c646b325`. The drift risk is broader than one threshold table: `src/lib/trust.js` defines 0/20/40/60/80 in `TIER_THRESHOLDS` but independently repeats the same boundaries across label, color, bar, text and grade helpers; `ScoreCard.js` keeps explanatory copy in a separate label-keyed object; and the default `npm test` path does not run score-policy boundary vectors.
>
> I also checked the adjacent repos before proposing an authority source. Backend v2 documents score provenance + calculation version for 0..100 scores but does not currently document an authoritative display-tier policy. The contract's documented verification tier is a separate 0..=10 business-profile field, so I would not conflate that with the frontend display bands.
>
> Approach:
> 1. First define the intended authoritative versioned display-policy contract (API schema or generated/checksummed fixture), including threshold, label, grade, styling/copy metadata and provenance.
> 2. Refactor all frontend score helpers/components to consume that policy instead of hard-coded boundaries or label-keyed parallel maps.
> 3. Fail safely on missing/malformed/future versions with an explicit unsupported/unavailable display state rather than stale fallback labels.
> 4. Add shared exact boundary vectors and a CI drift check that is actually invoked by the repository's default test/CI path; document compatibility and rollback behavior.
>
> I will preserve the contract's separate 0..10 verification-tier meaning unless maintainers explicitly bind the concepts. Ready to implement after assignment.
