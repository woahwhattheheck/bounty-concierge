# TrustLayer Frontend #6 — tier-policy authority baseline

Source-only, pre-assignment GrantFox readiness packet.

- Upstream: `TrustLayer-Org/TrustLayer-Frontend`
- Issue: https://github.com/TrustLayer-Org/TrustLayer-Frontend/issues/6
- GrantFox: provider route should be re-opened/revalidated immediately before application.
- Pinned upstream main observed: `9015d666984f1ae29657b6dfe2de3495c646b325`
- Live issue state at census: OPEN; no assignee; Development shows no branches or pull requests.
- Provider application: not claimed submitted by this packet.
- Upstream implementation: not started; maintainer assignment is required by the issue.

## Current policy topology

`src/lib/trust.js` is the effective tier authority today:

- tier minima: 0 / 20 / 40 / 60 / 80;
- labels: Untrusted / Low / Moderate / High / Excellent;
- independently repeated threshold branches for labels, color classes, bar classes, grades, and next-tier behavior;
- `PASSING_SCORE = 60`.

Consumers include `ScoreLegend`, `TrustBadge`, `ScoreMeter`, and `NextTierHint`. `ScoreCard` also owns a separate prose map keyed by display label, so a label rename/boundary change can drift explanatory copy from the underlying tier calculation.

The frontend `src/lib` tree has no `trust.test.js` at this census. CI runs the repository test command, storage tests, and build, but has no tier-policy vector/drift job.

## Cross-system authority gap

Current TrustLayer backend source exposes score computation and v2 provenance/calculation metadata but no tier thresholds, labels, grade/color semantics, or versioned tier-policy artifact/endpoint.

Therefore simply moving frontend constants into a local JSON file would reduce intra-frontend duplication but would **not** satisfy the issue's frontend/backend/contract drift guarantee. Before implementation, maintainers need to identify or establish the authoritative cross-system policy source.

## Assignment-ready design

Preferred contract:

1. Establish one versioned score-tier policy schema owned by the authoritative backend/shared contract surface: stable tier IDs, ordered minimums, labels, grade/semantic style token, passing threshold, and policy version.
2. Generate or publish a deterministic frontend-consumable artifact from that authority.
3. Parse it strictly; missing, malformed, unsupported-future, duplicate, unsorted, overlapping, or out-of-range policies fail safely rather than silently falling back to stale labels.
4. Map semantic style tokens to local CSS classes in the frontend so presentation classes are not backend data.
5. Key explanatory copy by stable tier ID, not display label.
6. Replace threshold branches with derivation from the policy object.
7. Share golden boundary vectors at 0/100 and min-1/min/min+1 for every threshold across backend/contract/frontend tests.
8. Add a deterministic CI drift check for the generated/pinned artifact and document rollback/version compatibility.

If maintainers require a frontend-only PR, they should name the authoritative generated-fixture source first; current main has no such source.

## Safety / authority

This evidence does not confer GrantFox assignment, reward eligibility, upstream write authority, payment authority, or permission to implement before maintainer assignment.
