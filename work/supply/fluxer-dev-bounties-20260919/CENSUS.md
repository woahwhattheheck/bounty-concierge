# Fluxer open dev-bounty census — 2026-09-19

Source refresh by **ZZ-Sol-Vela / GPT-5.6 Sol** using canonical `fluxerapp/fluxer-meta` issue threads and first-party/member/BountyHub-bot comments.

This packet exists to prevent the swarm from treating every dollar amount in an issue title as equally executable cash supply.

| Issue | Title amount | Canonical money evidence | Current blocker | Disposition |
|---|---:|---|---|---|
| #5 Threads | $750 title | BountyHub created $250, then +$250, +$250, +$50 = **$800 total** | Member `hampus-fluxer` asked submitters to **hold off** on 2026-07-13; no later member reopen observed | **HOLD_PAUSED** |
| #8 Activity Detection | $250 | BountyHub bot created **$250** | Member `Kamalaja` said work was in progress / by `vesaber`; later non-member comments say old work stopped and new PR creation is restricted, but no later member reopen/eligibility confirmation observed; many competing implementations | **HOLD_REOPEN_CONFIRMATION / SATURATED** |
| #19 Community Events | $250* title | Body says it **must be completed with #20** to receive the $250 dev bounty; no BountyHub-bot funding receipt observed in canonical comments | Multiple claims/design/reference implementations; contributor explicitly asks whether $250 is total or per issue and whether funded/escrowed; no member answer observed | **HOLD_ECONOMICS / SATURATED** |
| #20 Instance Controls / Safety | $250 title | Body says it **must be completed with #19** to receive $250 | Multiple overlapping carriers/claims; no BountyHub-bot funding receipt observed in canonical comments | **HOLD_ECONOMICS / SATURATED** |
| #21 User Calendars | $125 title | No BountyHub-bot funding receipt observed in canonical comments | Multiple claims/implementations and overlap with base Events/Calendar work | **HOLD_ECONOMICS / SATURATED** |
| #22 Advanced Calendar | $125 title | BountyHub bot created only **$10** on 2026-06-20 | Explicit unresolved contributor question: title $125 vs BountyHub $10; multiple claims and base-calendar dependency | **HOLD_AMOUNT_CONFLICT**; the only source-verified BountyHub amount is $10 |
| #9 Bot Commands | $500 title | Maintainer says **no BountyHub record**; intentionally placeholder until concrete spec | Empty body / no activated spec | **HOLD_PLACEHOLDER** (see `work/high-value/fluxer-9/`) |

## Notes

- #5 is the strongest reminder that **funded does not mean currently executable**: BountyHub reached $800, but a Fluxer member later paused submissions.
- #22 is the inverse: a high title amount does not override the only canonical BountyHub bot receipt ($10). Under the current owner economics fence, $10 belongs only in the small-bounty/save-up pile unless Fluxer resolves the amount upward.
- #19/#20 are coupled by their own bodies. Do not count them as $250 + $250 without a first-party clarification.
- #8 has a real $250 BountyHub record, but the current submission/reopen boundary is not source-green enough for a fresh full implementation.
- This census does not replace the separately refreshed/dispatchable #3 Events/Calendar packet, nor the active #2 Polls blocker work already owned elsewhere in the swarm.

No upstream issue, assignment, bounty claim, payment request, or contributor submission was mutated by this census.
