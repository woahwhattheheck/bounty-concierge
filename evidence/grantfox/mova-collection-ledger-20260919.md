# Mova / GrantFox collection ledger — 2026-09-19

Evidence-only collection receipt for the merged `Movalabs-crew/mova-store` backlog. This file does **not** assert GrantFox assignment, reward approval, payment, or eligibility. Advertised bounty values are issue-title amounts only.

## Clean mapped set

| PR | Issue | Advertised | Upstream merge commit | Account issue comment |
|---:|---:|---:|---|---|
| 363 | 25 | $90 | `6c5434c3a81cccac5cf2cde1be779c9c1c74b142` | `5562465355` |
| 371 | 18 | $100 | `5328da53fc0912ce36e46b91ebe6b90bc3387a7e` | none found |
| 369 | 67 | $100 | `b3caa95a55cf946eedea42dc1af3e7816430e888` | none found |
| 370 | 30 | $60 | `2cd56dc149d5730078f6d7e62e77779fe3407e3c` | `5562465745` |
| 374 | 94 | $95 | `1cb42209110cd03d9a0d80a8a146d17afcd8619b` | `5563136463` |
| 377 | 43 | $55 | `d86d0d9cab0dc33a8fc99ca82ff00aaee447691e` | `5563390079` |
| 361 | 75 | $70 | `26bca6f3b7e3871535854e255cb1dac0b2e1c406` | `5562465254` |
| 362 | 60 | $50 | `732ffbd735880e4d928a928e7fd2b4064a0eeed5` | none found |
| 359 | 52 | $60 | `06288cbfce95fcb3fc1e1cdc115721deb8201bad` | none found |
| 368 | 91 | $90 | `397bf334d6a97b2697485faf6d6c3bb6c70b8765` | `5562465648` |
| 376 | 105 | $65 | `1ec16986e5929d3a4ee4797cf080f8589c1cc29a` | `5563221149` |
| 375 | 86 | $90 | `091f20a4217e3e37f1671fc1a5afb136bf048460` | `5563158188` |
| 364 | 108 | $90 | `894afa8fa2ac49b8671478c8a9f261bfe6c79ae0` | `5562465449` |
| 365 | 29 | $80 | `05b177c49c877c23988a972429d16ace94d5cc98` | `5562465546` |
| 373 | 85 | $95 | `077589925efea7843deec973d51b12bb10f914a2` | `5563111344` |

Advertised total: **$1,190**.

All 15 PRs were fetched from GitHub and reported `state=closed`, `merged=true`, with `user.login=woahwhattheheck`.

## Application-state evidence

For 11 issues (#25, #30, #94, #43, #75, #91, #105, #86, #108, #29, #85), the current account's top-level issue comment explicitly records that a GrantFox application had **not** been filed and that no reward was being claimed. The exact comment IDs are pinned in the table.

No top-level `woahwhattheheck` issue comment was found on #18, #67, #60, or #52. Their mapped PRs are nevertheless independently verified merged above. Absence of a top-level comment is not evidence of absence of provider-side state; refresh GrantFox before any mutation.

Public GrantFox issue pages observed during this reconciliation expose **Apply to this issue**, describe applications as **1 application per user · Direct GitHub comment**, and show the sampled lanes as **Unassigned**. Provider state is mutable and must be refreshed immediately before applying.

Official GrantFox contributor guidance says the issue-page **Apply** action marks a contributor as a candidate; maintainers then review candidates and official assignment is the signal to begin assignment-gated work.

## Collection action contract

For an authenticated provider seat:

1. Refresh the exact GrantFox issue page immediately before mutation.
2. If `woahwhattheheck` is already registered as a candidate/application, do not duplicate.
3. Otherwise submit exactly one truthful application referencing the already-merged PR, the specific accepted work, and requesting campaign eligibility/reward review.
4. Do not assert prior GrantFox assignment, guaranteed reward, approval, or payment.
5. Capture the provider state and resulting GitHub comment/provider receipt.
6. Keep PR #404 excluded from new bounty claims.
7. Keep PR #388 / issue #23 separate and contested unless a fresh adjudication establishes a real eligible mapping.

## Authentication observation

An isolated provider-browser attempt on 2026-09-19 reached GrantFox's GitHub OAuth path but had no authenticated GitHub session or configured vault credentials. It submitted **zero** applications. A raw connector-created GitHub comment was deliberately not substituted for provider intake because the documented candidate-registration path is the GrantFox Apply action.

