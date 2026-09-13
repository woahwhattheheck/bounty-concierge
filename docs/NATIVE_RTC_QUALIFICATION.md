# Native RTC qualification

The RustChain Bounty Concierge browses and claims work paid in native RTC. The paid-work preflight must therefore recognize RTC itself as advertised compensation; requiring a dollar-denominated reward would block the product's own primary bounty currency.

## Currency boundary

`concierge.bounty_qualification` treats USD and RTC as independent reward currencies.

- Explicit `$...` bounty/reward forms keep the existing USD gate.
- Explicit `... RTC` bounty/reward forms satisfy the same paid-advertisement requirement.
- RTC is **never converted to USD** by qualification, even when issue prose includes a reference rate.
- Qualification reports separate `*_reward_usd` and `*_reward_rtc` signals so downstream code cannot accidentally treat a token amount as cash evidence.

## RTC source precedence

For the Elyan Labs/RustChain bounty surface, the live sponsor policy says the **title figure is authoritative** when older issue bodies retain a pre-adjustment amount. Qualification therefore uses this precedence:

1. RTC figure in a bounty/reward title;
2. high-confidence RTC reward declaration in the body, including `reward_rtc:` machine-readable specs;
3. explicit RTC amount in a live label.

When a title supplies an RTC amount, a different body amount remains visible in `body_reward_rtc` for auditing but does not create a false mismatch hold. A live RTC label that disagrees with the authoritative title/body reward still produces `REWARD_MISMATCH`.

## Fail-closed shapes

Qualification does not guess across a title range such as `15-60 RTC`: both endpoints are surfaced and dispatch is held as `AMBIGUOUS_ADVERTISED_REWARD`. Unrelated token prose such as a treasury or budget mention is not accepted as a reward unless it is tied to a bounty/reward declaration (or the issue is a bounty-labelled title with an RTC figure).

This gate establishes only that paid compensation is explicitly advertised and internally coherent enough to dispatch. It does not establish token-to-dollar value, payout probability, settlement, or cash revenue.
