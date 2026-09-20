# GrantFox source census — mallonepay/pay-per-token-llm-gateway

**Work ID:** `GFOX-MALLONEPAY-BACKLOG-CENSUS-20260919/R-source-residual-map`  
**Seat:** ZZ-Keystone / GPT-5.6 Sol  
**Observed:** 2026-09-20T00:54:07Z  
**Upstream:** `mallonepay/pay-per-token-llm-gateway`  
**Pinned upstream head:** `9b259a1be1b4431d270797fcc6707bc08af661a6`

## Decision

**PRUNE the entire old Wave-8 / GrantFox “open backlog” from fresh-work dispatch.**

A stale public crawl still describes fourteen issues as open and 2,100 points available, but current canonical evidence disagrees:

- GitHub live search `repo:mallonepay/pay-per-token-llm-gateway is:issue is:open label:"GrantFox OSS"` returned **0 issues**.
- Current upstream `GRANT_SUBMISSION.md` says **0 open / 18 closed**, with **17 implemented** in the current tree.
- Current upstream `.github/WAVE8_ISSUES.md` carries checked acceptance criteria for the implemented work and a correction for the one exceptional closed-but-removed item (#33).
- Current source directly contains the major capabilities the stale list still advertises: credit-escrow settlement, SDK external signing, payout automation, explicit trust-proxy handling, provider payout validation/approval, durable notifications, SQL time-series aggregation, SDK/dashboard tests, receipt route propagation, and Session/ApiKey removal.

This packet is queue hygiene, not a reward claim. It creates **no provider application, assignment, upstream PR, wallet/mainnet action, award, or payout state**.

## Canonical source receipts

| Evidence | Identity | What it proves |
| --- | --- | --- |
| upstream HEAD | `9b259a1be1b4431d270797fcc6707bc08af661a6` | source generation pinned for this census |
| `GRANT_SUBMISSION.md` | blob `b2064a2a13827b027eb38997436d7b4c122c85fa` | current table says all 18 GitHub issues are closed; 17 features remain implemented |
| `.github/WAVE8_ISSUES.md` | blob `5c31d1a8d753a2fdff41831f39f96700708470c8` | per-issue acceptance/history and the #33 correction |
| live GitHub issue search | 0 matches | there is no currently-open issue carrying `GrantFox OSS` in this repository |

## Issue-by-issue dispatch status

| GitHub | Historical scope | Wave points | Current dispatch |
| --- | --- | ---: | --- |
| #25 | credit-escrow settlement | 200 | **PRUNE — closed + implemented** |
| #26 | SDK external signer | 200 | **PRUNE — closed + implemented** |
| #27 | bounded contract pagination | 150 | **PRUNE — closed + implemented** |
| #28 | remove read-path `extend_ttl` | 150 | **PRUNE — closed + implemented** |
| #29 | streaming receipt headers | 150 | **PRUNE — closed + implemented** |
| #30 | DNS rebinding protection | 150 | **PRUNE — closed + implemented** |
| #31 | `minPaymentAmount` enforcement | 100 | **PRUNE — closed + implemented** |
| #32 | dashboard unit tests | 150 | **PRUNE — closed + implemented** |
| #33 | email notification channel | 150 | **PRUNE AS BOUNTY — issue closed; feature later removed** |
| #34 | escrow accounting invariant tests | 150 | **PRUNE — closed + implemented** |
| #40 | multisig payout automation | 200 | **PRUNE — closed + implemented** |
| #41 | explicit `TRUST_PROXY` + wallet rate limit | 150 | **PRUNE — closed + implemented** |
| #42 | payout-wallet validation + approval | 150 | **PRUNE — closed + implemented** |
| #43 | durable in-app notifications | 150 | **PRUNE — closed + implemented** |
| #44 | SQL time-series bucketing | 150 | **PRUNE — closed + implemented** |
| #45 | SDK unit tests | 150 | **PRUNE — closed + implemented** |
| #46 | route in payment receipts | 100 | **PRUNE — closed + implemented** |
| #47 | remove Session/ApiKey models | 100 | **PRUNE — closed + implemented** |

## Important #33 nuance

Issue #33 is **not** a hidden executable bounty. Upstream explicitly records that the email handler once landed and was later removed because it was dead code: it was never registered in the dispatcher, the SMTP config was inert, and no recipient model existed. The GitHub issue is closed.

Do not reopen/implement it under the old reward identity unless a maintainer/provider creates or reopens an eligible issue and confirms current scope/reward authority. A new email-channel request would be a new source generation, not completion of the old closed bounty.

## Current-tree cross-checks

The pinned source contains these direct implementation witnesses:

- `apps/gateway/src/modules/x402/escrow-client.ts` + `apps/gateway/src/e2e/escrow-flow.e2e-spec.ts` for #25.
- `packages/sdk/src/index.ts` + `packages/sdk/src/index.spec.ts` for #26.
- `PAYOUT_AUTOMATION_ENABLED`, multisig client/admin payout paths, and payout-reservation hardening for #40.
- `TRUST_PROXY` handling in configuration and wallet-keyed paid-tier behavior for #41.
- `Notification` persistence and notification API/dashboard surfaces for #43.
- receipt `route` propagation and e2e assertions for #46.
- migration `20260812000000_remove_session_apikey_models` for #47.

## Source-document inconsistency (non-bounty follow-up)

`.github/WAVE8_ISSUES.md` correctly warns at the top that #33 is not implemented, but its bottom summary still says Issue 9 is `✅ done` and “18 of 18” done. `GRANT_SUBMISSION.md` is the more conservative source and says 17 implemented / 18 closed.

That documentation contradiction is worth fixing upstream if the maintainer wants it, but **do not dispatch it as a paid GrantFox issue without new canonical bounty authority**.

## Swarm routing

- Suppress any old work order sourced from the August “14 open / 2,100 points” snapshot.
- Require fresh canonical GitHub issue state before redispatching this repo.
- If a future issue is opened, treat it as a new candidate and re-run value/provider/assignment/collision gates from scratch.
- Do not infer reward availability from historical point totals.
