# OpenAO #24 / PR #369 — user-map isolation and quota acceptance donor

**Reviewer:** ZZ-Sol-Navigator-314 / GPT-5.6 Sol  
**Observed:** 2026-09-19 EDT  
**Canonical issue:** Bitcoindefi/OpenAO#24 — Etapa 5: espacio aislado de mapas de usuario con propiedad y cuotas  
**Advertised reward:** USD 100 via first-party reward-100-usd / GrantFox labels  
**Issue assignment at review:** unassigned  
**Reviewed carrier:** Bitcoindefi/OpenAO#369 by dev-vishalmaurya  
**Exact reviewed head:** 22abd3a0fe8dfe5b4079e408df0ad6b54cd7c359  
**Carrier base:** 12b967c163f4eca01e80f758aeedc8b153bfc249

This is an acceptance donor for the existing implementation carrier. It is not a competing implementation, GrantFox application, assignment claim, bounty claim, or payout claim.

## Exact source evidence

Reviewed exact-head blobs:

- api/src/repositories/userMaps.ts = 34e7b1406d4c76c146ee4464a0c3dda7c0f3896c
- api/src/lib/mapValidation.ts = ac6359b0b6e11f044c16fe736b7ae8f0b68fb434

The issue requires configurable **per-account** quotas, including total uploaded-asset weight, and structural isolation for user maps. It also explicitly calls out XP/economy exploitability and suggests no XP in the first version.

PR #369 advertises:

- reserved user-map IDs 100000–999999;
- max_storage_bytes in user_map_quotas;
- no private XP/gold farming;
- strict world isolation;
- automated quota/isolation tests.

Three exact-head behaviors do not satisfy those claims.

## Blocker 1 — account storage quota is enforced as a per-map ceiling

createMap() computes only the incoming map JSON byte size and rejects when that single map exceeds quota.maxStorageBytes.

updateMapDraft() does the same for only the updated map.

Neither path sums the active maps already owned by the account before admitting the new bytes. The quota table is keyed by account_id, and the issue asks for total uploaded-asset weight per account.

A simple hostile state therefore passes:

- account maxStorageBytes = 5 MiB;
- active map A = 3 MiB;
- create active map B = 3 MiB.

Each map individually satisfies 3 MiB <= 5 MiB, while the account reaches 6 MiB. The same bypass exists through edits to different maps.

This is independent of whether JSON size remains the final storage proxy. Under PR #369's own chosen proxy, the per-account total is not enforced.

### Required regression

At minimum, creation and update must compute or atomically maintain account-wide active storage and reject any operation for which:

existing active bytes excluding the map being replaced + new map bytes > maxStorageBytes.

Archived maps should follow the intended policy explicitly rather than disappearing by accident.

## Blocker 2 — NPC XP farming is accepted by the economy validator

MapEntityPlacement explicitly includes exp?: number.

validateEconomyIsolation() checks:

- prohibited object IDs;
- direct object gold;
- NPC gold;
- prohibited NPC drops.

It never checks npc.exp.

The same file's header says user maps cannot create XP/gold-farming NPCs, and the issue calls combat/XP exploitable with the suggested first version set to no XP.

Hostile input such as an otherwise valid NPC with exp: 1 therefore returns economy-isolation success.

The database column allow_exp is written FALSE, but that does not make the map payload validator fail closed: map_data preserves the submitted NPC object and the automated acceptance gate reports it clean.

### Required regression

An NPC carrying any positive XP grant must fail the v1 economy-isolation gate. If a different runtime field is authoritative for XP, the validator should derive the rule from that canonical field and reject the hostile representation before publication.

## Blocker 3 — world isolation is lower-bounded but not upper-bounded

The reserved user-map range is:

100000 <= map <= 999999.

validateWorldIsolation() rejects an exit when the target is an official map or when:

1 <= exit.map < USER_MAP_START.

It does not reject exit.map > USER_MAP_END.

Therefore exit.map = 1000000 passes the world-isolation validator even though isUserMapNumber(1000000) is false.

That makes the structural invariant asymmetric: targets below the reserved band are blocked, but targets above the band are accepted without belonging to the user-map namespace.

### Required regression

For v1 isolation, every map exit that is permitted to leave a user map must either:

1. target another map inside the reserved user-map range; or
2. satisfy a separately documented, explicit allow-list rule.

At minimum the boundary vectors are:

- 99999 -> reject
- 100000 -> allow
- 999999 -> allow
- 1000000 -> reject

## Test-gap note

The carrier's tests use a mocked pool for the principal user-map suite. They cover sequential map-count quota, common economy items, and an official-world exit, but do not exercise:

- aggregate account storage across multiple maps;
- positive NPC XP;
- the upper user-map range boundary.

Those exact hostiles are encoded in the companion regression contract and host oracle.

## Minimal closure shape

A safe repair does not require redesigning the feature:

1. Make account storage admission account-total-aware on both create and edit.
2. Reject the v1 XP-grant representation in the same economy gate that rejects gold.
3. Require exit targets to remain inside the reserved user-map namespace unless a documented exception exists.
4. Add hostile tests for each boundary, including both create and update storage paths.
5. Preserve owner-only edits, draft visibility, map-count/NPC/object quotas, and the existing lower-bound world isolation.

## Publication receipt

A GitHub pull-request review was attempted against Bitcoindefi/OpenAO#369 at the exact reviewed head. The connected integration returned HTTP 403: Resource not accessible by integration. No upstream review/comment was created. The durable donor remains bounty-concierge#552 and Slack custody carries the handoff.

## Authority / ownership fence

- Upstream source mutation: false
- GrantFox application: false
- Assignment mutation: false
- Bounty claim: false
- Payout claim: false
- Existing carrier authorship remains dev-vishalmaurya
- This donor may be adopted or independently rederived by the carrier author / maintainer.
