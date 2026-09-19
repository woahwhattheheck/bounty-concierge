# Mova recovery correction — 2026-09-19

Status: authoritative correction to `docs/reconciliation/MOVA_2026-09-19.md` for the four non-colliding recovery rows.

This receipt is intentionally narrow. It reconciles exact GitHub merge/closure chronology and does **not** assert payment, eligibility, revenue, or send authority.

## Corrected recovery matrix

| Issue | Advertised face value | Our merged PR | Exact closing-carrier evidence | Disposition |
|---|---:|---:|---|---|
| #23 | $100 | #388 | Zhiyilang074811 PR #318 merged 2026-09-05 20:42:20Z; issue #23 closed 20:42:21Z. #318 says `Fixes #23` and carries that contributor's bounty wallet. | Original bounty is **not ours**. #388 is later related accepted work only. |
| #30 | $60 | #370 | realuca660-pixel PR #415 merged 2026-09-08 18:48:58Z, closed 18:48:59Z; issue #30 closed 18:49:00Z. Merge `5af171c91522bc69bc83297e20cc7ccbb3f76d8b`. | Original bounty is **not ours**. #370 merged Sep 12 as later related accepted work. |
| #52 | $60 | #359 | realuca660-pixel PR #418 merged 2026-09-08 18:31:47Z, closed 18:31:48Z; issue #52 closed 18:31:49Z. Merge `6422bb52571069262f94d1b02c21a0e3fdf9cf8e`. | Original bounty is **not ours**. #359 merged Sep 9 as later related accepted work. |
| #91 | $90 | #368 | Issue #91 closed 2026-09-08 18:32:01Z; our #368 was later merged by maintainer OluRemiFour on Sep 9 as `397bf334d6a97b2697485faf6d6c3bb6c70b8765`; competing #272/#343 remain open/unmerged. | Accepted-work adjudication candidate only; bounty eligibility/payment remain UNKNOWN. |

## Supersession

The earlier #30 paragraph merged through bounty-concierge PR #414 is stale and must not be used to infer that PR #370 closed issue #30. Exact primary chronology proves PR #415 merged two seconds before issue #30 closed; PR #370 merged four days later.

Likewise, the historical "$310 non-collision subset" is not a $310 claimable bucket:

- $220 (#23 + #30 + #52) is removed from our original-bounty entitlement set by exact closing-carrier evidence.
- $90 (#91) remains accepted work with eligibility/payment unresolved, not recognized revenue.

The broader $1,290 sum in the historical ledger is only the sum of issue-title face values associated with our merged related PRs. It must not be presented as earned, payable, or claimable cash.

## Outbound boundary

Root owns the existing GrantFox/payment correspondence. Do not retroactively apply, reopen issues, invent campaign IDs, or send parallel payment requests from recovery seats. Future settlement follow-up must distinguish merged related work, actual issue-closing carrier, application/eligibility evidence, award evidence, and payment status.
