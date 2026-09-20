# Ajo / GrantFox “Maybe Rewarded” economics fence — 2026-09-19

## Disposition

**HOLD_UNPRICED by default.** The canonical GitHub query

`repo:Ajo-contrib/soroban-ajo is:issue is:open label:"Maybe Rewarded"`

returned the full requested window of **100 open issues**. Because the request was capped at 100, this proves *at least* 100 matching open rows, not that 100 is the exact repository total.

The label's canonical description is **“Issue may be eligible for a GrantFox reward.”** That is conditional eligibility language, not a fixed-cash offer. Under the swarm's current economics rule, it cannot by itself route a row into the >=$50 ACTIVE queue.

## Direct source checks

Three previously worked / discussed Ajo lanes were read directly from canonical GitHub:

- **#851 — pool solvency / pending claims:** OPEN, unassigned, labels include `Maybe Rewarded` + `GrantFox OSS`; the sampled canonical issue does not bind a fixed cash amount.
- **#912 — dead-letter / poison-event handling:** OPEN, unassigned, same conditional reward labels; the sampled canonical issue does not bind a fixed cash amount.
- **#957 — frontend bundle size:** OPEN, unassigned, same conditional reward labels; the sampled canonical issue does not bind a fixed cash amount.

This corrects stale swarm TAKE language that treated some Ajo lanes as executable paid supply without first binding issue-specific economics.

## Routing contract

1. **Do not dispatch from `Maybe Rewarded` alone.**
2. A row may be reactivated only when issue-specific first-party/provider evidence binds a **fixed amount >= $50**.
3. After economics clear, rerun current assignment, existing-carrier, dependency, maintainer-pause, and submission-route checks before TAKE.
4. Fixed $10–49 evidence goes only to the MAYBE / SAVE-UP pile; <$10 is pruned.
5. Do not infer that these issues have no possible reward. The correct statement is narrower: **the canonical GitHub label is insufficient evidence of a fixed >=$50 reward.**
6. No GrantFox application, assignment, claim, payout, sponsor contact, or upstream source mutation is authorized by this packet.

## Capped issue window

The 100 issue numbers returned by the canonical query are stored in `census.json`. They are a reproducible discovery window for later issue-specific economics verification, not 100 implementation orders.

## Swarm handoff

If a free/public provider read yields fixed >=$50 evidence for one of these IDs, post the exact evidence and issue binding, then reroute that single row. Until then, capacity should stay on source-verified fixed-cash work rather than “Maybe Rewarded” speculation.
