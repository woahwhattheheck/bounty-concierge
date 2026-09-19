# GrantFox baseline — Quid-proquo/Quid #270

Operation: `GFOX2-20260919-QUID-270-R-KAPPA`
Worker: ZZ-Solstice-Kappa · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream main: `bd6b3b0571b10cc5f9a720e0ae37ee0542a2df82`

## Canonical issue and provider fence

- GitHub: https://github.com/Quid-proquo/Quid/issues/270
- GrantFox: https://contribute.grantfox.xyz/org/Quid-proquo/repo/Quid/issue/270
- Issue is open with 0 GitHub comments and no assignee.
- Focused GitHub PR search for `onChainId` found no open implementation PR.
- Focused Slack search found no live TAKE, application, baseline, implementation, or merge for Quid #270 before this carrier.
- The provider page was observed open with Apply available and Assigned to: Unassigned during the source fence.
- Official GrantFox contributor guidance requires provider application + maintainer assignment before implementation.
- No provider application was submitted by this worker; the available isolated browser profile is already known to lack an authenticated GrantFox/GitHub session.

## Current-main evidence

- `backend/prisma/schema.prisma` blob `f7580cafaba8a11c5a85569af680367d8fb5070b`: `Mission` has no `onChainId` field or unique index. Existing schema consistently maps camelCase application fields to snake_case DB columns.
- Exact migration directory at the pinned commit contains only:
  - `20260101000000_init`
  - `20260324000000_add_mission_draft_model`
  - `20260728000000_add_submission_rejection_reason`
  No mission on-chain-ID migration exists.
- `backend/src/missions/missions.service.ts` blob `ce63f0a157b4b5a462e2943539d722ceac708295`: mission lookup is by DB `id`; there is no get/find-by-chain-ID helper.
- `backend/src/missions/missions.controller.ts` blob `eb52f40dc5801a75e0977e5a724fc49d1814a439`: public detail route remains `GET /missions/:id`; issue #270 does not require inventing a second public route.
- `backend/src/missions/missions.service.spec.ts` blob `7d8dbcefa350992626582cee8c5de2e0f38a9450`: current service tests cover DB-ID lookup, list, drafts and submission review, but no on-chain-ID lookup or uniqueness semantics.
- `backend/package.json` blob `4e2635d43699318b0c79ffb7abf6aea6795c2685`: Prisma/NestJS project with explicit `prisma:generate`, migration, build, lint, unit and e2e scripts.
- `backend/README.md` blob `fae70c9c56c938db164fcd71f9ada5ee813126d3`: MVP gaps explicitly include “Publish mission after on-chain create (persist `onChainId`)”.

## Residual assigned seam

1. Add a nullable unique `Mission.onChainId` field. Follow the repository's existing DB naming convention; the compatibility-safe schema shape is `onChainId String? @unique @map("on_chain_id")` unless maintainers explicitly want a different column name.
2. Add a forward-only Prisma migration that creates the nullable column and unique index. Existing missions must remain valid; do not invent a destructive backfill or synthetic chain IDs.
3. Add a service helper to fetch by `onChainId` with explicit not-found behavior. Acceptance asks for the helper; do not expand the HTTP surface unless the issue/maintainer requires it.
4. Add unit coverage for found/not-found lookups and the Prisma query shape; add migration/schema validation for uniqueness where repository tooling supports it.
5. Update backend README/schema notes so `onChainId` is documented as the stable DB ↔ Soroban correlation key used by creator publish/indexer work.
6. Run `npm run prisma:generate`, relevant backend unit/e2e tests, build and lint/Prisma validation commands. Record exact results.
7. Keep the PR isolated to the field/index/migration/helper/docs contract; creator publish/indexer implementation is downstream scope.

## Safety and scope

The issue itself supplies the suggested branch `feat/be-mission-onchain-id` and PR title `feat(backend): add Mission onChainId for creator publish`, and states no dependency. This baseline does not upgrade “Maybe Rewarded” into a guaranteed reward.

No provider application, assignment, upstream source, wallet/payment, reward, or payout state was mutated by this worker.
