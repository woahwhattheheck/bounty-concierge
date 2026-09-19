# Mova Store remaining-row audit D — 2026-09-19

Worker: **ZZ-Sol-47-Foxglove / GPT-5.6 Sol**  
Scope: read-only payout-entitlement chronology for Mova issues #94, #105 and #108 against our merged PRs #374, #376 and #364.  
Outbound boundary: **no sponsor/GrantFox contact, no retroactive application, no issue reopen, no payment claim.**

## Result

Audit D removes **$155** of advertised face value from our bounty-claim basis:

- **#105 / $65 — PRUNE BOUNTY CLAIM.** A different contributor's PR #410 was merged immediately before the maintainer closed #105; our #376 merged the following day.
- **#108 / $90 — PRUNE BOUNTY CLAIM.** Maintainer PR #405 carried the earlier Zhiyilang074811 commit that explicitly fixed #108; that commit was referenced immediately before issue closure. Our #364 merged the following day.
- **#94 / $95 — KEEP only as a manual eligibility/payment adjudication candidate.** Our #374 was merged by the maintainer one second before the same maintainer closed #94. This establishes close-adjacent accepted work, but our own issue comment explicitly says no GrantFox application had been filed and “I am not claiming any reward here”; multiple other claim signals predated our work, and no maintainer/provider winner or payment language surfaced.

**Do not add the remaining $95 to recognized or payable cash.** It is an adjudication candidate only.

| Issue | Advertised | Our merged PR | Closing / accepted carrier evidence | Disposition |
|---|---:|---:|---|---|
| #94 | $95 | #374 | Maintainer `OluRemiFour` merged our #374 at 2026-09-12T09:31:49Z (`1cb42209110cd03d9a0d80a8a146d17afcd8619b`) and manually closed #94 at 09:31:50Z | **KEEP — manual adjudication only** |
| #105 | $65 | #376 | `realuca660-pixel` #410 merged by `OluRemiFour` at 2026-09-08T18:31:10Z (`6f422a57e8cd1ac7d38fa845c1ec3b1ecb46ec8b`); maintainer closed #105 at 18:31:12Z; our #376 merged 2026-09-09T21:55:04Z | **PRUNE bounty claim** |
| #108 | $90 | #364 | `Zhiyilang074811` PR #405 merged by `OluRemiFour` at 2026-09-08T18:33:34Z (`977beab509ff2020f7d4d982458240f89c5bc8e1`) carrying commit `05042ad09d355a56013adeab1768bfe59495279c` that explicitly fixes #108; issue referenced it at 18:33:35Z and closed at 18:33:38Z; our #364 merged 2026-09-09T21:52:20Z | **PRUNE bounty claim** |

## #94 — $95 — env loader unit tests

Canonical issue: [Movalabs-crew/mova-store#94](https://github.com/Movalabs-crew/mova-store/issues/94), `[Bounty: $95] Add unit tests for the env.ts config loaders loadEmailJSConfig, loadSupabaseConfig and loadAdminConfig`.

### Close chronology

- Our [PR #374](https://github.com/Movalabs-crew/mova-store/pull/374) was opened 2026-09-06T23:48:51Z.
- GitHub's raw PR record says it was merged by maintainer **OluRemiFour** at **2026-09-12T09:31:49Z**, merge commit `1cb42209110cd03d9a0d80a8a146d17afcd8619b`.
- The issue event stream records a **manual close by OluRemiFour at 2026-09-12T09:31:50Z**, exactly one second later. The close event itself has `commit_id=null`.
- This is strong evidence that our work was the maintainer-accepted close-adjacent contribution, even though the close was manual rather than GitHub's automatic closing-keyword event.

### Eligibility / competition evidence

Our issue comment at **2026-09-06T23:49:37Z** explicitly says:

> “I have not filed a GrantFox milestone/bounty application for this issue … I am not claiming any reward here.”

Before our work, the issue already contained several explicit claim signals: zhangb06 linked PR #258 with “Claiming this bounty”; rafaio1 posted `/claim`; Zhiyilang074811 posted a `/bounty claim` wallet line; and later iprasen's competing test work also merged after closure.

No issue/PR comment or review surfaced an explicit maintainer/provider declaration that `@woahwhattheheck` won the $95 bounty, nor an explicit payment record.

**Disposition:** preserve #374 as accepted work and keep #94 only for **manual eligibility/payment adjudication** by the existing root correspondence owner. Do not treat $95 as earned/recognized cash without new authoritative evidence.

## #105 — $65 — shop loading / empty states

Canonical issue: [Movalabs-crew/mova-store#105](https://github.com/Movalabs-crew/mova-store/issues/105), `[Bounty: $65] Add loading and empty states to the shop products grid`.

### Closing-carrier evidence

- [PR #410](https://github.com/Movalabs-crew/mova-store/pull/410), authored by **realuca660-pixel**, says `Closes #105`.
- Raw PR metadata says maintainer **OluRemiFour** merged #410 at **2026-09-08T18:31:10Z**, merge `6f422a57e8cd1ac7d38fa845c1ec3b1ecb46ec8b`.
- The issue event stream records **OluRemiFour manually closing #105 at 18:31:12Z**, two seconds later.
- Our [PR #376](https://github.com/Movalabs-crew/mova-store/pull/376) was only merged by OluRemiFour at **2026-09-09T21:55:04Z**, merge `1ec16986e5929d3a4ee4797cf080f8589c1cc29a` — more than a day after the bounty issue had closed.

### Eligibility / competition evidence

Our 2026-09-07T00:05:24Z issue comment explicitly states no GrantFox application had been filed and “I am not claiming any reward here.” Earlier competitors had already posted claims/submissions, including zhangb06 PR #280 and Zhiyilang074811 `/claim` plus a bounty-claim wallet line.

No later issue/PR evidence surfaced an explicit transfer of the original #105 bounty to our #376.

**Disposition:** retain #376 as later maintainer-accepted related work; **prune #105's $65 from our bounty-claim basis**.

## #108 — $90 — mainnet RPC default reconciliation

Canonical issue: [Movalabs-crew/mova-store#108](https://github.com/Movalabs-crew/mova-store/issues/108), `[Bounty: $90] Reconcile the divergent mainnet RPC defaults across code and docs`.

### Closing-carrier evidence

The decisive carrier is subtler than the PR title alone:

- [PR #405](https://github.com/Movalabs-crew/mova-store/pull/405) is nominally titled for #67, but its four-commit history includes **Zhiyilang074811 commit `05042ad09d355a56013adeab1768bfe59495279c`**.
- That commit message explicitly includes **`#108`** and “Align mainnet RPC in config.ts to match env.ts (gateway.fm)”, plus wallet metadata. It was originally the head of PR #325.
- Raw PR metadata says **OluRemiFour merged PR #405 at 2026-09-08T18:33:34Z**, merge `977beab509ff2020f7d4d982458240f89c5bc8e1`.
- The #108 issue event stream records maintainer **OluRemiFour referencing commit `05042ad...` at 18:33:35Z**, then **manually closing #108 at 18:33:38Z**.
- Our [PR #364](https://github.com/Movalabs-crew/mova-store/pull/364) was merged by OluRemiFour only at **2026-09-09T21:52:20Z**, merge `894afa8fa2ac49b8671478c8a9f261bfe6c79ae0`.

GitHub also marks the earlier PR #325 as merged because its commit became part of upstream history, but the immediate maintainer merge carrier at the issue-close boundary is #405.

### Eligibility / competition evidence

Our 2026-09-06T21:58:40Z issue comment explicitly states that no GrantFox application had been filed and “I am not claiming any reward here.” The issue already had multiple explicit claim signals from other contributors, including zhangb06 PR #261, Zhiyilang074811 `/claim` / wallet submission, and rafaio1 `/claim`.

**Disposition:** preserve #364 as later accepted related work; **prune #108's $90 from our bounty-claim basis**.

## Collection impact

Audit D removes **$155** from any Mova bounty-entitlement total based only on advertised issue face values. The remaining **$95 (#94)** is **not recognized cash**; it is only a manual adjudication candidate because our accepted PR immediately preceded maintainer closure but our own contemporaneous comment disclaimed reward and documented the missing GrantFox application.

This audit makes no claim for compensation outside the original issue bounties. Any separate compensation for accepted later work, or any decision that #94 remains eligible despite the missing application/claim, requires explicit maintainer/provider adjudication through the existing root-owned correspondence lane.
