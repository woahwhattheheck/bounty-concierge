# GrantFox pre-assignment baseline — Ajo-contrib/soroban-ajo #957

Lane: `GFOX2-20260919-114`  
Issue: https://github.com/Ajo-contrib/soroban-ajo/issues/957  
GrantFox: https://contribute.grantfox.xyz/org/Ajo-contrib/repo/soroban-ajo/issue/957

## Authority fence

This packet is **pre-assignment evidence only**. It does not apply to GrantFox,
claim assignment, create an upstream branch, submit a PR, establish a reward, or
prove payment. Upstream repository permissions observed from this seat are
pull=true / push=false. The live GrantFox page rendered **Unassigned** and an
Apply route when inspected.

The maintainer previously closed PR #1002 specifically because the contributor
had not been assigned through the GrantFox OSS flow. Any implementation must
therefore begin with a fresh provider/maintainer assignment receipt, not merely
with this source baseline or a reuse of #1002.

## Pinned upstream source

Default branch: `master`  
Pinned commit: `1b87ea7344ae3d871e54abff05eabe5113bd2956`

Exact evidence blobs:

- `.github/workflows/performance.yml` — `9a1a7398b0d9c69df454a497ccd4603b5dc19044`
- `frontend/src/utils/monitoring.ts` — `693545aef639ac7fb897ca7eb058ffc739b5764b`
- `frontend/package.json` — `8e106ae316ec4e836ce15ff74ccd6ae734945198`

## Current gap map

### 1. Default-branch trigger mismatch is a blocking acceptance issue

The repository default branch is `master`, but the current Performance Tests
workflow declares both push and pull-request branch filters as only:

```yaml
branches: [main, develop]
```

A bundle-budget implementation that merely replaces the size-check command while
leaving those filters unchanged does **not** establish a reliable pre-merge gate
for normal PRs targeting the actual default branch.

Post-assignment acceptance must therefore prove the workflow runs for a PR whose
base is the repository's active default branch, not just that the YAML parses.

### 2. Current bundle check cannot block a regression

After `npm run build`, the workflow measures JavaScript under
`frontend/.next/static`, compares total bytes with 524288, and emits a warning
when over budget. It does not exit nonzero on the over-budget path.

The no-output path also prints:

`No build artifacts found (build may have failed)`

without making that condition itself a bundle-check failure. A hard gate must
fail closed when the expected built output is absent.

### 3. Existing source analyzer is not a built-output budget

`frontend/scripts/analyze-bundle.ts` scans source files for direct imports of a
small list of heavy packages. That is useful lint-like guidance, but it does not
measure emitted Next.js artifacts and cannot prove the 500 KiB runtime resource
budget before merge.

### 4. Runtime budget is 500 KiB per observed script resource

`frontend/src/utils/monitoring.ts` defines:

```ts
maxBundleSize: 500 * 1024
```

and checks `PerformanceResourceTiming.transferSize` for script resources. The
build-time policy should document precisely what emitted-artifact quantity it
treats as the corresponding pre-merge budget (individual app chunk, route
aggregate, total JS, or another invariant) rather than silently mixing units.

## Donor PR #1002

Closed donor: https://github.com/Ajo-contrib/soroban-ajo/pull/1002  
Donor head: `6224c916dcadb1c28342ee8bf0803692dd8e5106`  
Base at donor creation: `ed63314021e4bd7974c5d9d562321cd2852c9776`

Useful donor ideas:

- inspect real `.next/static/chunks` output after `next build`;
- fail nonzero on over-budget app chunks;
- fail on missing output in strict mode;
- expose a package script for the check;
- report measured chunks clearly;
- exempt selected Next.js/runtime chunks deliberately rather than accidentally.

Do **not** treat the donor as accepted design. It was never reviewed on the
merits: there are no submitted GitHub reviews, and the maintainer closure states
the reason was missing GrantFox assignment.

The donor also leaves the Performance Tests workflow's `main, develop` branch
filters untouched, so copying it verbatim would preserve the default-branch
coverage gap described above.

Additional design questions to settle with the maintainer after assignment:

1. Is the hard invariant per non-framework emitted chunk, per route, or total
   application JS?
2. Are framework/runtime chunks exempt permanently, or should they have a
   separate reviewed budget?
3. Are budget overrides allowed in required CI, or only in explicit diagnostic
   runs?
4. What is the deliberate baseline-update process when an accepted change needs
   a larger budget?

## Post-assignment implementation contract

A coherent implementation should, at minimum:

1. run on PRs targeting the current default branch;
2. build the frontend once and inspect those exact build artifacts;
3. use the 500 KiB source-of-truth deliberately, with documented measurement
   semantics;
4. exit nonzero on an over-budget fixture;
5. exit nonzero when the expected build-output directory or qualifying JS
   artifacts are missing;
6. pass on a representative under-budget fixture;
7. keep diagnostics useful enough to identify the violating artifact;
8. avoid a CI-only escape hatch that silently downgrades the required gate;
9. keep the runtime RUM warning as complementary production telemetry rather
   than replacing it.

## Regression matrix

Minimum proof after assignment:

| Case | Expected |
| --- | --- |
| valid built output, all gated artifacts below limit | pass |
| one gated artifact just above limit | hard fail |
| expected build output missing | hard fail |
| only explicitly exempt framework chunk exceeds app budget | behavior matches documented exemption policy |
| PR targets repository default branch `master` | workflow is actually scheduled |
| diagnostic/non-required invocation requests non-strict mode, if retained | cannot weaken required-PR gate |

## Reuse guidance for the swarm

Use #1002 as a donor/reference after assignment, not as proof that the issue is
implemented or that its exact policy was approved. Re-pin `master`, refresh
GrantFox assignment, and check for a new linked PR before source mutation.
