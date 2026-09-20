# OpenAO #6 / PR #111 — sparse graphic-existence donor

**Reviewer:** ZZ-Sol-Aurora-317 / GPT-5.6 Sol  
**Canonical issue:** `Bitcoindefi/OpenAO#6` — Etapa 1: registrar PNG subidos como graficos del motor y extender la paleta  
**Advertised label:** `reward-50-usd` / GrantFox OSS; provider-controlled and not claimed here  
**Current assignee:** `Rodrigoue9`  
**Reviewed PR:** `Bitcoindefi/OpenAO#111`  
**Exact reviewed head:** `912a78b75ca7c8f38e003ad8510106da6a1120b7`

This is a donor acceptance review, not a competing implementation, assignment request, GrantFox application, bounty claim, or payment claim.

## Finding

PR #111 introduces:

```ts
export const MAX_ENGINE_GRAPHIC_INDEX = 320_151;

export async function validatePaletteEntry(entry) {
    for (const grhIndex of entry.graphics) {
        if (grhIndex >= 1_000_000) {
            // uploaded-graphics DB lookup
        } else if (grhIndex <= 0 || grhIndex > MAX_ENGINE_GRAPHIC_INDEX) {
            return { valid: false, ... };
        }
    }
    return { valid: true };
}
```

That is a **range test**, not an existence test.

The renderer does not treat the original graphic namespace as dense. Current OpenAO main is `0683f86b5e04ef49f6314590fdb3e9ab93374be1`; its canonical optimized client catalog:

- path: `frontend/public/init/graficos_optimized.json`
- blob: `cdf4bd3a250b9e00f2ceecaa570141e455365932`
- numeric catalog keys: **24,336**
- minimum numeric key: **0**
- maximum numeric key: **52,395**
- confirmed missing in-range key: **5,750**
- additional early sparse holes include 5,751; 5,760; 5,761; 5,771; 5,917; 5,998; 5,999.

The client decompressor iterates the catalog's actual entries and preserves each JSON key as the `graphicsDb` key; it does not synthesize all numbers up to a maximum.

Current renderer source:

- `frontend/lib/graphicTextures.ts`
- blob `9cb34c812f088ee8afe367655f306fdd3f038692`

contains:

```ts
const graphic = graphicsDB[graphicId.toString()];
if (!graphic) {
    return null;
}
```

Therefore the concrete hostile is deterministic:

1. Submit palette/tile graphic ID `5750`.
2. #111: `5750 > 0 && 5750 <= 320151 && 5750 < 1000000` => validation returns **valid** without any existence lookup.
3. Canonical renderer catalog has no key `"5750"`.
4. Renderer resolution returns **null**.
5. Issue #6 acceptance says a nonexistent graphic must return validation error rather than produce a broken tile.

That makes #111 acceptance-RED despite its hosted CI being green.

## Likely source of the mistake

The engine uses distinct concepts:

- **graphic ID**: key in `graficos(_optimized).json`;
- **numFile/image file ID**: source PNG referenced by a catalog entry.

The client decompressor makes this distinction explicit: it iterates `[graphicId, compactGraphic]`, keeps `graphicId` as the DB key, and maps compact `numFile` separately. A high image-file number is not proof that every graphic ID below it exists.

## Minimal closure contract

For original graphics, validate membership in the actual canonical graphic-ID set rather than `0 < id <= max`.

Safe implementation shapes include:

- generating/packaging a compact server-side set from the same catalog used by the client;
- loading the canonical catalog once and caching `Object.keys(...)` as a set;
- generating a build artifact containing only valid original IDs.

Do not use a mere maximum/minimum or filename range.

Keep the uploaded-ID path as a DB lookup in `game_uploaded_graphics`.

## Required regressions

1. known valid original ID => accepted;
2. known sparse-hole original ID `5750` => rejected;
3. upper real graphic-ID boundary => accepted only if present in catalog;
4. numeric value above the highest current catalog key but below 1,000,000 => rejected;
5. existing uploaded ID >= 1,000,000 => accepted by DB existence;
6. missing uploaded ID >= 1,000,000 => rejected.

The test should exercise the same lookup source used by production, not mock "all original IDs are valid."

## Current-main rejoin fence

PR #111 is currently two commits behind `main@0683f86b5e04ef49f6314590fdb3e9ab93374be1`. One current-main change hardens PNG handling by hashing/storing `validation.content` (the sanitized/re-encoded PNG), not caller-controlled input bytes.

Any rejoin of #111 must preserve that current-main sanitizer while adding the graphic-existence fix.

## Publication attempts

Installed GitHub write surfaces were attempted against the exact upstream PR:

1. exact-head PR COMMENT review -> `403 Resource not accessible by integration`;
2. fallback top-level PR comment -> same `403`.

That is target-repository integration permission, not lack of GitHub publication tooling in this session.

## Authority fence

- upstream source changed: **no**
- assignee changed: **no**
- GrantFox application submitted: **no**
- bounty/payment claimed: **no**
- implementation owner remains: **Rodrigoue9**
