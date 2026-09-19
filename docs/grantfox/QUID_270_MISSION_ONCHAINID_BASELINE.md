# GrantFox source baseline — Quid-proquo/Quid #270

Operation: `GFOX3-20260919-QUID270/R-onchainid-baseline`  
Worker: ZZ-Solstice-Helix · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `bd6b3b0571b10cc5f9a720e0ae37ee0542a2df82`

## Canonical issue and authority

- GitHub issue: https://github.com/Quid-proquo/Quid/issues/270
- Title: **[BE] Ensure onChainId on Mission (+ migration)**
- GitHub state at this fence: open, zero comments, no assignee.
- Campaign labels: `GrantFox OSS`, `Maybe Rewarded`, `Official Campaign | FWC26` plus backend/creator/priority.
- Targeted open-PR search for issue 270 returned no matching open carrier.
- Upstream connector repository metadata is `pull=true`, `push=false`.
- The issue declares **Depends on: None** and asks for branch `feat/be-mission-onchain-id` / PR title `feat(backend): add Mission onChainId for creator publish`.
- This packet is source evidence only. It does not perform a GrantFox application, obtain assignment, mutate upstream code/database, or establish a reward/award/payment.

## Current source gap

### Prisma schema

Pinned source: `backend/prisma/schema.prisma@f7580cafaba8a11c5a85569af680367d8fb5070b`.

The `Mission` model currently contains only the internal UUID `id`, owner/title/CIDs/metadata/reward/participant/status/summary/timestamps and relations. There is no `onChainId` field and no unique chain-ID index.

The schema consistently maps camelCase TypeScript fields to snake_case database columns with `@map(...)` (for example `ownerAddress -> owner_address`, `rewardAmount -> reward_amount`, `createdAt -> created_at`). A source-aligned implementation should therefore prefer:

```prisma
onChainId String? @unique @map("on_chain_id")
```

unless the maintainer explicitly chooses an unmapped camelCase database column.

The field should remain nullable. Existing missions predate this correlation key; inventing a destructive/default backfill would create false chain identity.

### Migration history

Pinned initial migration: `backend/prisma/migrations/20260101000000_init/migration.sql@dbe0052b567c3c6f42d3ccae2c42a37da2086ac8`.

Pinned tree contains only:

- `20260101000000_init`
- `20260324000000_add_mission_draft_model`
- `20260728000000_add_submission_rejection_reason`

There is no mission chain-ID migration. After assignment, add a **forward-only** migration that adds nullable `on_chain_id TEXT` and a unique index. Do not edit the initial migration.

PostgreSQL unique indexes permit multiple `NULL` values, so nullable + unique is compatible with existing un-published rows while enforcing one DB mission per non-null chain ID.

### Service seam

Pinned source: `backend/src/missions/missions.service.ts@ce63f0a157b4b5a462e2943539d722ceac708295`.

`MissionsService.getMission(id)` already uses `prisma.mission.findUnique` and throws:

```ts
new NotFoundException(`Mission ${id} not found`)
```

There is no get-by-chain-ID helper. After the Prisma unique field exists, add a focused `getMissionByOnChainId(onChainId: string)` (or maintainer-preferred equivalent) using `findUnique({ where: { onChainId }, ... })` and the same explicit not-found pattern.

The issue asks for a helper, not a public controller route. Do not expand API surface unless maintainers or acceptance criteria require it.

### Test seam

Pinned source: `backend/src/missions/missions.service.spec.ts@7d8dbcefa350992626582cee8c5de2e0f38a9450`.

The test harness already mocks `prisma.mission.findUnique` and covers `getMission` found/not-found behavior. The chain-ID helper can be tested in this existing suite without a new fixture framework:

- found: asserts `where: { onChainId: "..." }` and returns the mission;
- missing: returns null and raises `NotFoundException`;
- schema/migration evidence: validate that generated Prisma accepts the unique selector and the migration creates the unique index.

Uniqueness enforcement is fundamentally database/schema behavior; do not fake a service-level duplicate check that races the database.

### README / product seam

Pinned source: `backend/README.md@fae70c9c56c938db164fcd71f9ada5ee813126d3`.

The README's MVP-gap list explicitly says:

> Publish mission after on-chain create (persist `onChainId`)

That makes the intended DB ↔ Soroban correlation semantics source-explicit. After assignment, update the README to state that `Mission.onChainId` is the nullable unique stable correlation key used by future creator-publish/indexer flows. Do not claim the publish/indexer flows themselves are implemented by this issue.

## Assignment-ready implementation map

Only after provider/maintainer assignment:

1. Add `onChainId String? @unique @map("on_chain_id")` to `Mission`, following existing mapping style.
2. Generate a new forward migration; add nullable `on_chain_id` and its unique index. Leave old migrations untouched.
3. Regenerate Prisma client so `onChainId` is available as a unique selector.
4. Add `MissionsService.getMissionByOnChainId(...)` with existing detail include and `NotFoundException` behavior.
5. Add focused service tests for found and not-found chain IDs; ensure the mock type permits the new selector.
6. Add a schema/migration assertion or integration check showing the unique index exists. Let the DB enforce uniqueness.
7. Update backend README correlation-key documentation only; keep creator publish/indexer implementation out of scope.
8. Run backend gates from the pinned package scripts: `npm run prisma:generate`, `npm run build`, `npm test -- --runInBand`, and `npm run test:e2e` if the repository test environment supports it. Use `npx prisma validate` / migration validation as appropriate for the Prisma version.

## Acceptance traceability

| Issue requirement | Pinned current state | Post-assignment target |
| --- | --- | --- |
| unique nullable `onChainId` | absent | nullable Prisma field + unique index |
| migration applied | only init/draft/rejection migrations | new forward migration |
| helper get-by-on-chain-id | only get-by-DB-id exists | focused unique lookup helper |
| documented | README says persistence is future work | document stable DB ↔ chain key |
| preserve existing missions | no chain ID today | nullable column, no invented backfill |
| avoid scope creep | publish/indexer still scaffold/future | do not implement publish/indexer in this issue |

## Verification / scope fence

This is static source analysis at the pinned upstream commit. No migration was executed against a live/shared database, no upstream branch/PR was created, no GrantFox application/assignment was performed, and no reward/payment is claimed.

"Maybe Rewarded" remains discretionary campaign metadata, not proof of a fixed payout.
