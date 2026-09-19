# Ajo-contrib/soroban-ajo #957 — frontend bundle-size readiness baseline

Status captured 2026-09-19 by ZZ-SolForge / GPT-5.6 Sol.

## Authority / assignment fence

- GitHub issue: Ajo-contrib/soroban-ajo#957 — OPEN, unassigned, labels include `GrantFox OSS`, `Maybe Rewarded`, `Third Campaign`, `frontend`, `performance`.
- GrantFox public page resolves and currently says **Unassigned** with an **Apply to this issue** route.
- Upstream repository access for the connected account is read-only (`pull=true, push=false`).
- Prior implementation PR #1002 is CLOSED and unmerged. Maintainer Christopherdominic closed it explicitly because the author was not assigned through the GrantFox OSS flow, and asked contributors to obtain GrantFox assignment before resubmitting.
- Therefore this packet is pre-assignment source/readiness evidence only. It does **not** authorize implementation, provider application, upstream writes, submission, wallet/payment, or reward claims.

## Pinned source generation

Default branch: `master`
Commit: `1b87ea7344ae3d871e54abff05eabe5113bd2956`

Relevant blobs:

| Path | Git blob | Finding |
|---|---|---|
| `.github/workflows/performance.yml` | `9a1a7398b0d9c69df454a497ccd4603b5dc19044` | Builds frontend and computes total built JS, but budget overage only prints a warning; missing artifacts also only print a message. |
| `frontend/src/utils/monitoring.ts` | `693545aef639ac7fb897ca7eb058ffc739b5764b` | Runtime ResourceTiming checks `PERFORMANCE_BUDGETS.maxBundleSize = 500 * 1024` only after a user downloads scripts. |
| `frontend/scripts/analyze-bundle.ts` | `1b7812566db396b372e3fdb3706e6503ba6af7c6` | Source-import scanner for heavy packages; it does not enforce actual `.next` build output size. |
| `frontend/package.json` | `8e106ae316ec4e836ce15ff74ccd6ae734945198` | Has `next build` and `@next/bundle-analyzer`, but no deterministic hard budget test command. |

## Exact residual gap

The current `frontend-bundle` workflow already measures built output:

```sh
TOTAL=$(find frontend/.next/static -name "*.js" -exec du -b {} \; | awk '{sum+=$1} END {print sum}')
BUDGET=524288
if [ "$TOTAL" -gt "$BUDGET" ]; then
  echo "⚠️ Bundle size exceeds budget!"
else
  echo "✅ Bundle size within budget"
fi
```

This step exits successfully on over-budget output. It also exits successfully when `frontend/.next/static/chunks` is missing. Consequently the CI status cannot currently enforce the issue's pre-merge budget contract.

The runtime monitor is not a substitute: `observeResourceTiming()` checks individual downloaded script transfer sizes against 500 KiB and only logs in development / emits telemetry after download.

The existing `analyze-bundle.ts` is complementary source analysis, not built-output measurement.

## Historical carrier #1002

PR #1002 implemented a hard Node-based check and wired it into the performance workflow, but was closed unmerged specifically for missing GrantFox assignment. Its design should be treated as prior art, **not** as an automatically reusable accepted patch.

One important semantic difference should be resolved deliberately after assignment: current workflow measures **total built JS at 512 KiB**, runtime monitoring uses **500 KiB per script**, while #1002 gated **500 KiB per non-framework app chunk** and exempted framework chunks. A successor should define one documented CI metric/budget instead of silently changing the existing policy.

## Recommended assigned implementation

After official GrantFox or maintainer assignment:

1. Extract a deterministic build-output budget checker that accepts an artifact root and explicit byte budget.
2. Fail non-zero for:
   - over-budget output,
   - missing build artifact directory,
   - zero matching JS artifacts / malformed measurement.
3. Preserve a human-readable report with exact bytes, budget, largest contributors, and preferably write the same numbers to `$GITHUB_STEP_SUMMARY`.
4. Add fixture-driven tests that prove below-budget PASS, above-budget FAIL, and missing-artifact FAIL without requiring a full Next build.
5. Wire the helper into `.github/workflows/performance.yml` after `npm run build`; keep artifact upload under `if: always()`.
6. Resolve and document whether the enforced contract is total JS, per-chunk, or both, and why its byte threshold aligns (or intentionally differs) from the runtime 500 KiB signal.
7. Keep testnet-only environment values already used by the build job; no funded wallet or mainnet flow is necessary.

## Acceptance evidence

- exact assigned upstream branch / commit;
- focused checker tests with exit codes for PASS / over-budget / missing-output;
- workflow syntax + command readback;
- one deliberate CI regression proving an oversized fixture fails the job;
- final PR linked to #957 and assignment evidence.

This packet makes no statement that a reward is fixed or guaranteed.
