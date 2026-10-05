# Commitlabs Frontend #1934 — preserve mock database read failures

Issue: https://github.com/Commitlabs-Org/Commitlabs-Frontend/issues/1934

## Complete source

Apply the two files under this packet's `src/` directory to the same paths in an ordinary upstream-compatible fork. Base is upstream `master` at `0d847032ef5862806cbb678f0bca93b6be44fb17`; the original `src/lib/backend/mockDb.ts` blob is `7dfb7051401f53a7ec010124000853a8e289eda9`.

Postimages:
- `src/lib/backend/mockDb.ts`: `fcfbaf841fcb771af87970ae6acc5cd7ce814c00`
- `src/lib/backend/__tests__/mockDb.test.ts`: `7be93fd7a6a85097f1c73ca4e65baddbd0278762`

The change distinguishes ENOENT from all other read/parse failures, returns fresh empty arrays for a missing file, logs a content-free diagnostic through the existing logger, and rethrows the original failure. Valid data normalization and the existing write queue remain unchanged. Four focused test cases cover the three requested failure modes and ordinary valid records.

## Actual execution

Node 22.16.0 / TypeScript 5.8.3. The real production TypeScript was transpiled and executed against a temporary real filesystem for missing, malformed and valid JSON; permission error and logger were doubled. Baseline: one pass, three failures. Candidate: four passes, zero failures. The supplied Vitest test source also transpiles without syntax diagnostics.

Vitest, the full Next.js app, project typecheck, and project CI were NOT executed. Node 22 here is outside the project's declared Node 20 range; this local result does not replace the project runner. The full execution harness is retained in this packet for reproducibility.

Recommended focused project command after the ordinary fork is available:
`pnpm exec vitest run src/lib/backend/__tests__/mockDb.test.ts`

## Submission

Suggested PR title: `fix(backend): preserve mock database read errors`
Suggested body:

Closes #1934.

Only a missing mock database file now returns fresh empty collections. Invalid JSON and filesystem errors are logged without stored contents and rethrown instead of appearing to be an empty database. Valid record normalization and the write queue are unchanged. Four focused regression cases are included.

Validation so far: isolated execution of the actual transpiled production source on Node 22.16.0 / TypeScript 5.8.3, using temporary files and permission/logger doubles, changed baseline 1 pass / 3 fail into 4 pass / 0 fail. Full project Vitest/typecheck/build/CI are not claimed.

## Publication status

This is a complete source packet, not an upstream PR or bounty award. At intake, the canonical issue was OPEN/unassigned with GRANTFOX OSS / MAYBE REWARDED / Official Campaign FWC26 labels; one outside assignment request was present, but no assigned contributor or matching PR was found. No fixed reward or payment is asserted.

The intended claimant's ordinary `woahwhattheheck/Commitlabs-Frontend` fork returned 404 and a same-user repository search returned no fork. Reuse the existing ordinary-fork publication operation for Commitlabs #1942. Do not rebuild #1942 or create a second fork request. Once that fork exists, submit this independently against upstream master, preserving any newer source changes and current assignment decisions. No additional local-PC task is requested.
