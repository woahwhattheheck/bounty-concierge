# Commitlabs #1942: executed timer lifecycle repair

## Product result

The candidate preserves the existing clear function and fixes its module-reload boundary. The in-memory store and its cleanup interval share one namespaced global state. A retained export from an older module copy clears the currently active timer; a subsequent module evaluation starts one timer again. Node timers are unreferenced, while numeric browser handles and timer shims without `unref` are supported. Record fields, TTL behavior and all service methods are unchanged.

Only two files are intended for the upstream contribution:

| Path | Exact Git blob |
| --- | --- |
| `src/lib/backend/idempotency.ts` | `4f116f46a536ce09afc640b094c1f19684dddce1` |
| `src/lib/backend/__tests__/idempotency.cleanup.test.ts` | `85d4012714e93b7a3dc7fdbdc8d7f6ae33e08c9d` |

Their complete postimages are the two correspondingly named root files on this validation branch. Upstream baseline is `Commitlabs-Org/Commitlabs-Frontend@0d847032ef5862806cbb678f0bca93b6be44fb17`; original product blob is `554a8a2b36ae0243cfdf6b86195c6ca51d3364da`. The existing 33-case unit file, blob `4cf5936c22c558dbbbd5c3e077684374479c1a54`, was not modified.

## Actual execution

[GitHub Actions run 37201398150](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37201398150) completed successfully at controller `b5d257496d04bc514c45b458324b5fcecaf2a84b`.

Environment: Node **20.20.2**, isolated Vitest **2.1.9** and coverage-v8 **2.1.9**. The runner satisfies the declared Vitest range, but is not the complete project dependency install or original jsdom setup.

| Selection | Passed | Failed | Pending/skipped |
| --- | ---: | ---: | ---: |
| Original product, six new lifecycle cases | 1 | 5 | 0 |
| Candidate, same six cases plus 33 unchanged existing unit cases | 39 | 0 | 0 |

The five baseline failures exercise duplicated timers/records after module evaluation, the extra surviving timer after reload cleanup, an old export not controlling the restarted timer, missing singleton/unref behavior, and repeated registration of numeric/shim timer handles. The no-timer-runtime control passes on both versions.

Coverage of the changed production section, lines 77–105: **19/19 statements, 7/7 branch outcomes and 1/1 named function covered**. All reported branches in the full module are covered. Full-module line/statement coverage is **84.76%**, not 100%; unmodified `getdel`, `incr` and `expire` logic is outside the retained unit selection. The changed-logic coverage exceeds the contributing guide's 95% requirement without adding unrelated tests.

A separate real Node 22.16.0 check evaluated the transpiled production module six times. Original: six created/referenced intervals, no shared replay through the newest module, five intervals still live after the oldest clear. Candidate: one created interval, zero referenced intervals, shared completed-record replay, zero intervals left after the oldest clear. A plain candidate import also lets an ordinary Node process exit without an explicit clear. This is a lifecycle/resource-count observation, not an application throughput or deployment benchmark.

## Raw evidence and identity

[Artifact 11302842820](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37201398150/artifacts/11302842820) contains raw baseline/candidate JSON and logs, the exact source/test, product diff, V8 coverage, Node/dependency versions and the isolated runner lockfile. ZIP: **28,975 bytes**, SHA256 **c7860bba5d43caef0bc952be7d9988d800dce7213772e7b6bcd8265571c794fa**. The downloaded ZIP was independently hashed; both extracted postimages exactly match the local candidate and the blobs above.

Two push-triggered validation runs were created. The normalized commit-run lookup returned an empty list, but a direct branch-runs read subsequently exposed the actual runs. This report binds the successful run above; no third execution was requested. Future publication should reuse the retained result for these unchanged postimages rather than rerun it because one wrapper returns an empty list.

## Publication state and limits

This branch is a complete tested source packet, **not an upstream PR or accepted bounty**. An ordinary upstream-compatible fork under the intended claimant and the current campaign decision are still needed for submission. The issue is open and tagged GRANTFOX OSS / MAYBE REWARDED; a fixed reward, award and payment are not established here. Preserve earlier contributor work and any current ownership/assignment decision when submitting.

Do not merge this isolated controller branch into Bounty Concierge main. No production deployment, full project typecheck, full build, original project CI, maintainer acceptance or cash receipt is claimed.
