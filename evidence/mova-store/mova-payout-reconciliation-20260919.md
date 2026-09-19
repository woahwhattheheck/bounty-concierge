# Mova Store merged-bounty reconciliation — 2026-09-19

Owner/session: **ZZ-Sol-Variegate / GPT-5.6 Sol**  
Contributor identity from authenticated GitHub connector: `@woahwhattheheck`  
Upstream: `Movalabs-crew/mova-store`  
Purpose: durable **GET-PAID / reconciliation** evidence only. This packet does **not** assert an award, entitlement, approved amount, or completed payment.

## Executive result

GitHub readback verifies **16 merged PRs** by `@woahwhattheheck` linked to issues whose titles advertised bounty amounts totaling **$1,290**.

The upstream write connector cannot create the consolidated payout-review issue: two `create_issue` attempts against `Movalabs-crew/mova-store` returned provider **403 `Resource not accessible by integration`**. Collaborator-permission introspection on the external repo returned the same provider class, while normal repository reads report pull=true/push=false. Treat this as an integration-installation boundary, not proof that the human GitHub account cannot publish through another authenticated surface.

A browser carrier was also attempted via the available authenticated-browser surface; at packet publication time it had not started execution. Do not represent it as submitted without a later terminal receipt.

## Exact merge ledger

| PR | Issue | Merge commit | Merged at UTC | Advertised issue-title amount |
|---|---|---|---|---:|
| #363 | #25 | `6c5434c3a81cccac5cf2cde1be779c9c1c74b142` | 2026-09-12T09:43:11Z | $90 |
| #371 | #18 | `5328da53fc0912ce36e46b91ebe6b90bc3387a7e` | 2026-09-12T09:42:16Z | $100 |
| #369 | #67 | `b3caa95a55cf946eedea42dc1af3e7816430e888` | 2026-09-12T09:34:25Z | $100 |
| #370 | #30 | `2cd56dc149d5730078f6d7e62e77779fe3407e3c` | 2026-09-12T09:33:42Z | $60 |
| #374 | #94 | `1cb42209110cd03d9a0d80a8a146d17afcd8619b` | 2026-09-12T09:31:49Z | $95 |
| #377 | #43 | `d86d0d9cab0dc33a8fc99ca82ff00aaee447691e` | 2026-09-12T09:31:43Z | $55 |
| #361 | #75 | `26bca6f3b7e3871535854e255cb1dac0b2e1c406` | 2026-09-09T22:04:16Z | $70 |
| #362 | #60 | `732ffbd735880e4d928a928e7fd2b4064a0eeed5` | 2026-09-09T22:04:10Z | $50 |
| #359 | #52 | `06288cbfce95fcb3fc1e1cdc115721deb8201bad` | 2026-09-09T22:02:20Z | $60 |
| #368 | #91 | `397bf334d6a97b2697485faf6d6c3bb6c70b8765` | 2026-09-09T22:00:11Z | $90 |
| #376 | #105 | `1ec16986e5929d3a4ee4797cf080f8589c1cc29a` | 2026-09-09T21:55:04Z | $65 |
| #375 | #86 | `091f20a4217e3e37f1671fc1a5afb136bf048460` | 2026-09-09T21:54:42Z | $90 |
| #364 | #108 | `894afa8fa2ac49b8671478c8a9f261bfe6c79ae0` | 2026-09-09T21:52:20Z | $90 |
| #365 | #29 | `05b177c49c877c23988a972429d16ace94d5cc98` | 2026-09-09T21:51:22Z | $80 |
| #373 | #85 | `077589925efea7843deec973d51b12bb10f914a2` | 2026-09-09T21:48:29Z | $95 |
| #388 | #23 | `3cd6edacf1a955f11c1b264c7ef9ff459643e114` | 2026-09-09T21:43:43Z | $100 |
|  |  |  | **Advertised total** | **$1,290** |

All 16 PRs were fetched from upstream and returned `merged=true`.

## Process / eligibility truth fence

Earlier issue comments by `@woahwhattheheck` explicitly disclosed that no GrantFox milestone/bounty application ID had been filed before implementation and that the contributor was not then claiming a reward. That disclosure must travel with any collection request.

Upstream currently contains consolidated payout-request precedent:

- #434 — 16 merged PRs by `@iprasen`, advertised claim total $1,295.
- #435 — 10 merged PRs by `@bilhokista`, advertised claim total $625.
- #439 — 2 merged PRs by `@ElvinGts`, advertised claim total $170.

Those issues are open in the observed snapshot. They demonstrate a GitHub reconciliation channel, **not** proof that a particular request has been approved or paid.

## Collision / double-payment audit

This ledger must not be treated as sixteen automatically payable bounties.

Issue IDs in this ledger that also appear in #434:
`#18 #25 #29 #43 #67 #75 #85 #86 #94 #105 #108` — **11 entries / $930 advertised**.

Issue IDs in this ledger that also appear in #435:
`#60 #75 #86 #108` — **4 entries / $300 advertised**.

#439 additionally overlaps #25.

Union across #434/#435/#439: **12 of this ledger's 16 issue IDs / $980 advertised** have another contributor's consolidated payout request. Existing #435 itself explicitly flags #86 as a double-claim risk.

The remaining four issue IDs not present in those three observed ledgers are:
- #30 — $60
- #52 — $60
- #91 — $90
- #23 — $100

That is **$310 advertised**, but even these are not represented here as approved awards; other program state may exist outside the three payout-request issues.

## Safe upstream request text

Use this only through an authenticated upstream publication surface:

> **[Payout Review] Reconcile 16 merged advertised-bounty PRs (@woahwhattheheck — $1,290 issue-title total)**
>
> Please review these 16 merged contributions under the program's actual assignment/award rules. The $1,290 figure is only the sum of the advertised issue-title amounts and is not a claim that it has already been awarded or approved. Earlier issue comments disclosed that no GrantFox milestone/application ID had been recorded before implementation.
>
> Existing consolidated requests #434/#435/#439 overlap several linked issues, so please de-duplicate under the authoritative assignment/acceptance/award rules rather than paying the same bounty twice.
>
> Please confirm (1) which entries are recognized as eligible and at what approved amount, (2) whether retroactive GrantFox reconciliation is supported or GitHub is the settlement channel, and (3) which payout rail/details are required once eligibility is established. No unverified wallet/payment destination should be invented.

Attach or paste the exact ledger above.

## Next dependency

One authenticated upstream publication route is required to create the reconciliation issue or equivalent provider claim. If the maintainer confirms eligible entries, then capture the exact approved amount, payout rail, provider receipt/transaction reference, and whether funds are merely approved versus actually received. Only then should the standing owner notification/email receipt be sent.

Do not spend further inference on new Mova microbounties while this merged-value backlog is unresolved.
