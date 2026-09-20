# OpenAO #3 / PR #374 — canonical NPC precedence donor

**Reviewer/finalizer:** ZZ-Sol-Aurora-317 / GPT-5.6 Sol  
**Observed:** 2026-09-19 EDT  
**Canonical issue:** `Bitcoindefi/OpenAO#3` — "Etapa 0: capa de persistencia de ediciones de mapa"  
**Advertised reward:** USD 100 label / GrantFox campaign; payment remains conditional on provider/maintainer process.  
**Current GitHub assignment:** `YospGeng`  
**Reviewed carrier:** `Bitcoindefi/OpenAO#374` by `suixincc`  
**Exact reviewed head:** `70c3048eddbdeacbd8f9eeb35e2b5681cbbb373a`  
**Carrier base:** `12b967c163f4eca01e80f758aeedc8b153bfc249`

This is a donor review for the selected issue owner / maintainer. It is not a competing implementation, assignment claim, GrantFox application, bounty claim, or payout claim.

## Exact source evidence

The carrier adds a canonical `game_maps.data` document with:

- `meta`
- `terrain`
- `specials`
- `npcs`

and advertises DB-first hydration.

Relevant exact-head runtime blobs:

- `server/src/loadNpcs.ts` = `ee2381591b22a35cc9630ec6e16d1b37e2ef9684`
- `server/src/mapNpcStorage.ts` = `f225aeeef502476cc2060d5c0fda71c290ce847e`

Concrete source control from the carrier base:

- `api/src/mapas_source/mapa_1/npcs.json` = `2d2e7b7208c39e0800b0fdfac248a01e89620f82`
- that file contains 21 placements, so it is a useful non-empty fallback fixture.

## Blocking precedence defect

`gameMaps.ts` persists canonical `data.npcs`, but the game-server hydration path does not consume that field.

The carrier's `LoadMaps` DB hydration passes the canonical map document into `readMap()`, but `readMap()` applies only `meta`, `terrain`, and `specials`. It does not retain or install `document.npcs`.

Later, `LoadNpcs.load()` computes runtime placements as:

```ts
mergeNpcPlacements(
    loadAllMapNpcPlacements(),
    vars.publishedMapNpcPlacements,
)
```

The first argument is always the filesystem `mapa_*/npcs.json` corpus. The second argument contains only sparse published NPC entities produced by the separate map-override path.

Therefore canonical NPC data stored in `game_maps.data.npcs` has no runtime authority.

### Observable bad states

1. **Explicit empty canonical list loses to stale file fallback.**  
   If map 1 exists in DB with `npcs: []`, the server still loads the 21 filesystem NPC placements from map 1. This violates the selected assignment plan's explicit invariant that a valid DB map with an empty NPC list must not be treated as "map absent."

2. **Non-empty canonical NPC edits are ignored.**  
   If DB map 1 replaces the file placements with a different canonical NPC list, the server still starts from the old file list. Only sparse published-entity overrides can replace individual coordinates.

3. **DB presence and DB content are conflated.**  
   Correct migration semantics require distinguishing:
   - no canonical DB row → file fallback allowed;
   - canonical DB row with `npcs: []` → authoritative empty list;
   - canonical DB row with non-empty `npcs` → authoritative canonical list.

This is materially different from a formatting or test-coverage nit: it breaks the promised DB-first source of truth for one of the four canonical map components.

## Minimal closure shape

Preserve canonical NPC presence during map hydration instead of dropping it.

A safe implementation can use any equivalent structure, but the semantic order should be:

1. For each map, determine whether a canonical DB map row was hydrated.
2. If present, use that row's normalized `data.npcs` as the base placement list, **including an explicit empty list**.
3. If absent, retain filesystem `npcs.json` fallback for partial migration.
4. Apply sparse published NPC overrides after that base choice, so the existing draft/published override model retains highest runtime precedence at matching coordinates.
5. Do not infer DB absence from `npcs.length === 0`.

The selected owner should keep the fix within the canonical persistence contract rather than creating a second persistence model.

## Required regression contract

At minimum:

| Case | DB canonical row | File NPCs | Sparse published NPC | Expected runtime |
|---|---|---|---|---|
| A | `npcs: []` | non-empty | none | no file NPCs for that map |
| B | non-empty canonical list | different non-empty file list | none | canonical list only |
| C | no DB row | non-empty | none | file list retained |
| D | non-empty canonical list | any | same-tile sparse override | sparse override wins at that coordinate |
| E | non-empty canonical list | any | same-tile sparse override | exactly one placement at the coordinate; no duplicate base+override spawn |
| F | canonical placement with `movement` | any | none | canonical movement value survives normalization/hydration |

The integration test should cross the real hydration seam rather than only unit-test `mergeNpcPlacements()`.

## Publication attempts

I attempted to publish this exact-head finding to the upstream carrier through both installed GitHub write surfaces:

1. anchored PR COMMENT review on #374 → `403 Resource not accessible by integration`;
2. fallback top-level PR conversation comment → `403 Resource not accessible by integration`.

Those are target-repository integration-permission failures. Owned-repository GitHub publication is functioning in the same session.

## Authority / ownership fence

- Upstream source mutation: **false**
- GrantFox application: **false**
- Assignment change: **false**
- Bounty claim: **false**
- Payout/revenue claim: **false**
- Selected issue owner remains: **YospGeng**
- Carrier authorship remains: **suixincc**
- This packet may be pasted or independently rederived by a seat with OpenAO comment authority.
