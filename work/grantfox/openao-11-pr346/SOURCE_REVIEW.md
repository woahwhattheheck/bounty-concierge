# OpenAO #11 / PR #346 — live-publication convergence review

**Reviewer:** ZZ-Sol-Sundial-426 / GPT-5.6 Sol  
**Canonical issue:** \`Bitcoindefi/OpenAO#11\` — live map publication without server restart  
**Advertised reward:** first-party \`reward-100-usd\` label  
**Issue state:** OPEN, unassigned, dependency-gated  
**Reviewed carrier:** \`Bitcoindefi/OpenAO#346\`  
**Exact carrier head:** \`5f37ec59e4a17f57421273e3810d85170434ff1b\`  
**Current upstream main:** \`0683f86b5e04ef49f6314590fdb3e9ab93374be1\`  
**Existing repair donor:** \`Aduersarius/OpenAO#3\` @ \`4ea280f5e3ef9401e922d44e42a9da9ca13b7f31\`

This is a donor/acceptance review. It is not a competing implementation, assignment request, GrantFox application, bounty claim, or payment claim.

## Verdict

Raw PR #346 is **HOLD_ACCEPTANCE**. Do not merge its current head as the final #11 implementation.

The important convergence path already exists: donor PR #3 is based directly on #346's exact head and contains repairs plus regression/e2e evidence. The next useful action is to converge #3 (or equivalent fixes) into #346, rejoin current upstream main, then rerun required checks and live acceptance. Opening another full #11 implementation would duplicate active work.

## Acceptance blocker 1 — removed client overrides do not restore the static baseline

Current \`frontend/utils/gameLoader.ts\` refreshes an already-mutated \`MapData\` object:

\`\`\`ts
export async function refreshMapOverridesInPlace(mapData, mapNumber) {
    invalidateMapCache(mapNumber);
    await applyMapOverrides(mapData, mapNumber);
}
\`\`\`

\`applyMapOverrides()\` only applies rows currently returned by \`/maps/:id/overrides\` and returns immediately when \`overrides.length === 0\`.

That means a publication sequence can be:

1. static tile layer 1 is graphic \`101\`, walkable;
2. published override changes it to graphic \`202\`, blocked;
3. a later publication removes that override;
4. API correctly returns no row for the removed override;
5. live refresh applies nothing to the already-mutated map;
6. the connected player still has graphic \`202\` / blocked state until a full baseline reload.

This directly conflicts with #11's requirement that a player already in the map sees the newly published version without reconnecting.

Donor #3 closes this by loading a detached static baseline, applying the current published override set to the fresh baseline, generation-fencing overlapping loads, and only then copying graphics/blocked state back into retained tile identities.

## Acceptance blocker 2 — server blocking baseline is aliased by layer

Current \`server/src/gameDataSync.ts\` keys snapshots by x/y/layer, but each snapshot stores \`tile.blocked\`, which is tile-level state. The database model explicitly allows multiple rows for the same x/y across different layers: conflict identity is \`(map_num, x, y, layer, status)\`.

Concrete failure:

1. base \`(10,10)\` has no \`blocked\`;
2. layer 1 publication is \`blocked=true\`; its snapshot captures base \`undefined\`, then sets \`blocked=1\`;
3. layer 2 publication on the same tile has \`blocked=null\`; its distinct layer-keyed snapshot captures the already-mutated \`blocked=1\`;
4. next publication removes both overrides;
5. rollback of layer 1 deletes \`blocked\`;
6. rollback of layer 2 restores its polluted snapshot \`blocked=1\`.

The final server state is blocked even though the base tile is walkable and no published override remains.

Donor #3 changes the snapshot scope to one x/y tile record: blocking is captured once per tile, graphics remain per-layer, original values are restored before applying the next complete publication, and client \`blockMap\` deltas are derived from actual before/after terrain.

## Acceptance blocker 3 — map data refresh does not redraw retained Pixi tiles

Current \`incomingUiPackets.ts\` recognizes the machine marker and calls:

\`\`\`ts
void refreshMapOverridesInPlace(engine.mapData, liveReload.mapNum);
\`\`\`

but does not request a redraw of already-retained scene tiles after mutating \`engine.mapData\`.

Donor #3 returns changed coordinates from refresh and calls \`ctx.redrawMapTiles(...)\` after a successful refresh. Its committed evidence reports that this was necessary for the visible pixels to change/restore without reconnecting.

## Existing donor is the shortest convergence path

\`Aduersarius/OpenAO#3\` is OPEN and mergeable, base = raw #346 head, current donor head:

\`4ea280f5e3ef9401e922d44e42a9da9ca13b7f31\`

Relevant donor blobs:

- \`frontend/utils/gameLoader.ts\` — \`3b781df2ef4a3841d652bda1d0aa2e249efb9eb3\`
- \`server/src/gameDataSync.ts\` — \`a5550ded9b7707d5c376895773b302cf78d67bce\`
- \`frontend/components/game/session/incomingUiPackets.ts\` — \`6886a96df9c50c3c2a9ffbc5fdcb17757ff235bf\`

The donor PR body and committed artifacts report:

- client regression suite: original **4/10 pass**, patched **10/10 pass**;
- server regression suite: original **5/13 pass**, patched **13/13 pass**;
- final local two-client stack: **19 named assertions passed**, after an additional renderer-redraw fix.

Those execution numbers are **author-supplied evidence observed in the donor PR**, not tests independently rerun by this reviewer.

## Current-main fence

#346's base is \`12b967c163f4eca01e80f758aeedc8b153bfc249\`. Current upstream main is two commits ahead at \`0683f86b5e04ef49f6314590fdb3e9ab93374be1\`.

The intervening main delta touches ten paths, including two that overlap #346:

- \`api/src/repositories/worldBuilder.ts\`
- \`api/src/server.ts\`

It also contains the PNG re-encoding/sanitization work and NPC placement changes. A convergence must preserve current-main behavior; do not blindly replace the overlapping files with the old carrier versions.

## Closure contract

A future green decision requires all of the following:

1. #3 or equivalent repairs are incorporated into the #11 carrier;
2. current upstream main is rejoined without regressing its overlapping \`worldBuilder.ts\` / \`server.ts\` changes;
3. removed published overrides restore static terrain/blocking on both client and server;
4. multi-layer rows cannot corrupt the tile-level blocking baseline;
5. stale/overlapping client refreshes cannot win over newer publication;
6. retained scene tiles are redrawn for changed coordinates;
7. ordinary players still never observe draft rows;
8. repository-required API/server/frontend/protocol/security checks pass on the converged head;
9. live acceptance is rerun against the converged head, not inherited from a predecessor;
10. maintainer/provider assignment and payment remain separate decisions.

## Upstream publication attempt

The installed GitHub publication surface was used to post this review to \`Bitcoindefi/OpenAO#346\`, but the provider returned:

\`403 Resource not accessible by integration\`

That is a target-repository integration-permission failure, not absence of GitHub write tooling. The durable evidence therefore lives in this repository and Slack for a publisher/maintainer to consume.

## Authority

- upstream source changed: **no**
- assignee changed: **no**
- GrantFox application submitted: **no**
- bounty/payment claimed: **no**
- TinyFish / metered browser used: **no**
