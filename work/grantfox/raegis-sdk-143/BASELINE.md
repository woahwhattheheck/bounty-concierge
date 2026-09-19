# GrantFox collision baseline — Raegis-RWA/Raegis-sdk #143

Operation: `GFOX-RAEGIS-143-COLLISION-PARALLAX-Q7N4-20260919`
Worker: ZZ–Parallax-Q7N4 · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream main: `69fff2c7e8c6fe801428fc1bb71d064c5c94949c`

## Disposition

**REUSE / REVIEW OPEN PR #157 + MAINTAINER DELTA CLARIFICATION. Do not start a parallel eligibility model for #143.**

Issue #143 asks for a compliance readiness checker with approved, blocked, revoked, pending, unknown, unavailable states; typed reason codes; tests; compliance limitations; dashboard guidance; and investor/admin applicability.

Fresh provider/source census:
- GitHub #143 is open, unassigned, and its issue page shows no directly linked development branch or PR.
- GrantFox #143 is Unassigned / Apply enabled and displays one contributor comment: `panditdhamdhere` — “I would like work on this.”
- That same contributor owns **open PR #157**, `feat: add investor eligibility explanation mapper (Closes #52)`, head `647a5a52b4b70973171bc3a5bf89cf478110c011`.
- PR #157 is not merged and targets main. It changes 11 files (+875/-2).

## Why #157 collides materially with #143

PR #157 already implements:
- typed `InvestorEligibilityStatus` for approved, blocked, revoked, unknown, unavailable;
- stable reason codes and suggested next actions;
- safe fixed messages;
- `InvestorModule` live/pure explanation APIs;
- tests for all five states, aliases, unknown/future statuses, conflicting signal priority, invalid address, and safe error handling;
- dashboard-facing documentation;
- a structural non-guarantee disclaimer with `verified: false`;
- an explicit rule that bare `checkWhitelist(): boolean` cannot prove “revoked.”

Current main does **not** contain those eligibility files yet: `src/investor/` still exposes only `portfolio.ts`, and `src/types/` has no eligibility model. The collision is therefore with a live open PR, not with already-landed main.

## Clean #143 residual if maintainers want it distinct

1. Add/prove **pending** state authority. Do not infer pending from a bare whitelist false.
2. Define **admin-operation readiness** composition. #157 is address/investor compliance explanation, not an admin-operation authorization proof.
3. Reuse #157’s public types/reason architecture if it lands; do not create a second eligibility union.
4. Preserve the evidentiary rule: revoked/pending/admin-approved states require a source that actually proves them.
5. Obtain a current hosted-CI receipt for whichever carrier is used. #157’s body reports local `npm run check` with 145 tests passed but its CI checkbox remains unticked in the observed PR body.

No provider application, assignment, upstream source mutation, reward, payment, or wallet action was performed by this worker.
