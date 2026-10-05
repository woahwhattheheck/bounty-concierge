# Commitlabs Frontend #1936 — duplicate registry validation

Issue: https://github.com/Commitlabs-Org/Commitlabs-Frontend/issues/1936

Current upstream source already replaces the impossible duplicate-object-key check with duplicate `.code` detection. The maintained test still does not construct a duplicate, so the issue's failure-mode acceptance remains uncovered.

`PATCH.diff` makes the smallest remaining change:
- keep `validateErrorCodeRegistry()` unchanged for production callers;
- allow an explicit registry fixture for validation;
- use that registry for duplicate, empty-code, and required-field checks;
- add one focused test with two distinct keys carrying `BAD_REQUEST`.

Focused isolated validation: TypeScript 5.8.3 strict compile succeeded; Node 22.16.0 execution returned valid=true / duplicates=[] for the default fixture and valid=false / duplicates=['BAD_REQUEST'] with the expected duplicate error for the synthetic collision.

Observed upstream base: Commitlabs-Org/Commitlabs-Frontend master @ 0d847032ef5862806cbb678f0bca93b6be44fb17.

Recommended project check after publication:
`pnpm exec vitest run src/lib/backend/errorCodes.test.ts`

Suggested PR title: `test(error-codes): exercise duplicate registry detection`

Suggested body begins: `Closes #1936.`

Publication status: complete patch packet only. At intake #1936 was open/unassigned with GrantFox OSS, Maybe Rewarded, and Official FWC26 labels. Reuse the existing Commitlabs ordinary-fork publication operation already carrying #1942/#1934; do not create another fork request. Refresh source and assignment immediately before applying.
