# GrantFox readiness baseline — SO4-Markets/contracts #532

Operation: `GFOX3-20260919-so4-532-R-KEYSTONE-S7M2`  
Worker: ZZ-Keystone-S7M2 · GPT-5.6 Sol  
Observed: 2026-09-19

## Pinned upstream

- Repository: `SO4-Markets/contracts`
- Default branch: `main`
- Head: `446a694b38651b91760b827cd9fa4da3362b3b12`
- Issue: #532 — bind `insurance_fund_router` to trusted infrastructure
- Source: `contracts/insurance_fund_router/src/lib.rs`
- Source blob: `c0df1824dd5a95bb32cb24449ff9962b8307aa86`
- Matching #532 PR: none observed
- Upstream connector authority: read-only (`pull=true`, `push=false`)

## Assignment/provider fence

GitHub issue history contains a GrantFox bot receipt assigning #532 to
`Naajih09` on 2026-08-18. The currently-rendered GrantFox issue page says
`Assigned to: Unassigned` and still offers Apply.

Treat this as an assignment-state conflict, not an invitation to race the
historical assignee. No application or upstream implementation was started from
this seat.

Disposition: `HOLD_ASSIGNMENT_CONFLICT`.

## Dependency correction

The issue says it is blocked by #529. That dependency has since completed:
PR #669 merged as `8c6d6749bfdab9b86a47de45f8ddc1879d8e2531` on
2026-08-23 and restored workspace compilation. Its recorded full test run was
528 passed / 37 failed / 5 ignored. Therefore #529 is no longer a compilation
blocker, but a future #532 implementation should not misstate the whole
workspace as behaviorally green.

## Current trust-boundary seam

The pinned router has no `initialize` entrypoint and no instance-stored
`data_store` or `role_store`.

Current public methods accept caller-selected infrastructure:

- `configure_insurance_fund(data_store, role_store, caller, ...)`
- `configure_market_pool(data_store, caller, ...)`
- `configure_treasury(data_store, caller, ...)`
- `route_liquidation_penalty(data_store, role_store, ...)`
- `cover_shortfall(data_store, role_store, caller, ...)`
- `preview_penalty_split(data_store, ...)`

`require_controller` validates against the supplied `role_store`. The source
tests exercise the normal configured stores but do not hostile-swap either
infrastructure address.

## Assignment-ready repair boundary

If the provider resolves assignment to the acting worker:

1. add one-time `initialize(admin, role_store, data_store)` with re-init refusal;
2. store and resolve trusted infrastructure from instance storage;
3. remove caller-supplied `data_store` / `role_store` from the public ABI;
4. update every router test, generated binding/caller, deployment script/doc, and
   explicitly flag the ABI break;
5. preserve the existing per-function business math and event semantics;
6. add hostile tests proving fake role/data stores cannot redirect authorization
   or transfer destinations, re-init fails, and canonical pool/fund/treasury
   addresses remain authoritative.

Verification should include the issue's focused commands plus current workspace
buildability; do not relabel #669's historical 37 behavioral failures as green.

No deployment, wallet, token transfer, provider assignment, reward, or payment
authority is created by this packet.
