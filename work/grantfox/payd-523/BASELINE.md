# GrantFox candidate baseline — Protocol-Guild/PayD #523

Operation: `GFOX-DISCOVERY-PAYD-523-ZZ-SOLSTICE-20260919`  
Worker: ZZ-Solstice · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `af5c348e83033ed3340e589b68e8554f0303060e`

## Candidate

- Issue: https://github.com/Protocol-Guild/PayD/issues/523
- Title: Remove dead duplicate `contractEventsController.ts`
- State: OPEN / GitHub-unassigned.
- Labels observed: `backend`, `easy`, `Maybe Rewarded`, `GrantFox OSS`, `Third Campaign`.
- Matching open implementation PR found: **no**.
- Existing issue application comments: **1**.

The existing application comment proposes Soroban storage, Rust WASM, authorization invariants, and gas profiling. Those topics do not match this issue's TypeScript controller-deduplication scope. It is still real application pressure and must not be ignored; it is not evidence that the requested controller cleanup is already implemented.

## Exact current-source evidence

Two controller files coexist on pinned main:

- `backend/src/controllers/contractEventsController.ts` (plural), blob `ead7682b49e0c6ab98f91f9208f33c8c60f62db1`.
- `backend/src/controllers/contractEventController.ts` (singular), blob `4f8a0852043f2f282ce7b3bae1f2fc0f44cbed06`.

Repository search makes the canonical direction unusually clear:

- `backend/src/routes/contractEventRoutes.ts` imports the **singular** `ContractEventController`.
- The dedicated controller tests import the **singular** controller.
- `IMPLEMENTATION_SUMMARY.md`, `PR_CHECKLIST.md`, `CONTRACT_EVENT_INDEXER.md`, the quickstart, and architecture docs all name the singular controller.
- The plural controller appears in its own file and in `backend/src/__tests__/contractIntegration.test.ts`, where that test imports **both** singular and plural controllers.

The implementations are not byte duplicates. The singular controller is the richer organization-scoped API with pagination/filtering/indexer status. The plural controller is therefore not safe to choose as canonical merely because the issue calls the files duplicates; current routing/docs/tests identify the singular controller as the existing canonical surface.

## Bounded implementation plan after provider assignment

1. Refresh upstream main, issue assignment, issue comments, and open PRs.
2. Confirm the integration test does not assert intentional behavior unique to the plural controller.
3. Delete `contractEventsController.ts`.
4. Remove/update the plural import and any plural-controller assertions in `contractIntegration.test.ts` while preserving coverage through the singular controller/routes.
5. Do not refactor `ContractEventController` or alter endpoint behavior.
6. Run the repository's focused contract-event tests plus normal backend test/typecheck/lint commands required by current main.
7. Report exact upstream head and validation results.

## Suggested application approach

A good application should stay narrow: explain that current routes/docs/dedicated tests already point to the singular controller; propose deleting the plural file, updating the integration test's duplicate import/use, and proving routes/tests remain green. Do not claim Soroban contract or Rust work for this TypeScript cleanup.

## Authority

This is candidate discovery and current-source evidence only. It does not submit a provider application, confer assignment, authorize upstream implementation, or establish reward/payment eligibility.
