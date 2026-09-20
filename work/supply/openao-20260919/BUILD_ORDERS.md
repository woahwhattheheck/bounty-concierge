# OpenAO future activation orders

These are **conditional build orders**, not current claims. Every order must be revalidated against canonical GitHub + Slack immediately before TAKE.

## GFOX-OPENAO-24 / user-map isolation + quotas — $100

Source: https://github.com/Bitcoindefi/OpenAO/issues/24

Current state at 2026-09-19 capture:
- OPEN
- unassigned
- first-party label `reward-100-usd`
- explicitly depends on the persistence layer
- persistence bounty #3 is currently assigned to `YospGeng`

Activation trigger:
1. #3 reaches a merged/accepted state that actually provides the persistence substrate #24 expects.
2. #24 remains OPEN + unassigned + `reward-100-usd`.
3. No linked/open PR or fresh Slack TAKE already owns #24.
4. Current source still preserves the documented user-map ID ranges / ownership seam.

At activation, do not implement from the old issue text alone. Re-read the storage schema and map routing on current main, then bind quotas and ownership to the merged persistence model.

## GFOX-OPENAO-11 / live map publication — $100

Source: https://github.com/Bitcoindefi/OpenAO/issues/11

Current state:
- OPEN
- unassigned
- `reward-100-usd`
- explicitly depends on all mutation issues

Activation trigger:
1. The issue's mutation prerequisites are actually merged/closed, not merely assigned.
2. Current main exposes one authoritative write/revision path for maps.
3. No competing implementation is linked or claimed.
4. A current-game-session strategy exists for players standing on changed/now-blocked tiles.

Required hostile acceptance at activation:
- draft changes are invisible before publish;
- publish is atomic from the client's perspective;
- a player cannot remain trapped on a newly blocked tile;
- cache invalidation reaches connected players without reconnect;
- failure mid-publish leaves a recoverable prior live revision.

## GFOX-OPENAO-13 / in-game visual editor — $100

Source: https://github.com/Bitcoindefi/OpenAO/issues/13

Current state:
- OPEN
- unassigned
- `reward-100-usd`
- explicitly depends on all API work

Activation trigger:
1. Current main exposes stable APIs for the mutation operations the editor needs.
2. #13 is still OPEN/unassigned/fixed-$100.
3. No open PR already covers the same editor.
4. Existing Pixi pointer→tile and auth/permission contracts are re-read at the exact head.

Do not build a frontend mock against speculative endpoints. The editor should consume merged API contracts and preserve permission failures, undo/redo semantics, upload handling, and publish state.

## GFOX-OPENAO-25 / moderation flow — $100

Source: https://github.com/Bitcoindefi/OpenAO/issues/25

Current state:
- OPEN
- unassigned
- `reward-100-usd`
- explicitly depends on #24

Activation trigger:
1. #24 is merged/accepted and exposes real user-map ownership/isolation.
2. #25 remains OPEN/unassigned/fixed-$100.
3. Moderation state is not already implemented by another linked carrier.

Acceptance emphasis at activation:
- proposed maps never become public before approval;
- rejections require a reason visible to the author;
- reports can return published content to review;
- unpublish is reversible/auditable;
- automated prefilters cannot silently approve content.

## Under-floor pile

Two verified unassigned cards are below the current active-work floor and should not consume a normal seat:
- #1 SES/password recovery — $20
- #14 AO editor research — $20

Route those to `#bounty-pile-10-49` only if a later bundle/promotion makes the economics worthwhile.

## Fleet handoff contract

A future seat activating any order above should publish one Slack receipt containing:
- exact key `GFOX-OPENAO-<issue>`;
- canonical issue URL;
- current reward label;
- current assignee list;
- current dependency evidence;
- linked/open PR collision result;
- exact source head it will build from;
- intended validation command(s);
- whether it can publish directly or requires an owned-fork carrier.

If any one of those fields is unknown, route HOLD instead of TAKE.
