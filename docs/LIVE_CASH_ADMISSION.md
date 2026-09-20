# Live cash admission

bounty_live_cash_admission is the source-bound front door for new GitHub
bounty economics. It exists because a deterministic receipt is not trustworthy
when the same caller can supply reward amount, authority, evidence URL, and a
historical evaluation time.

## Trust boundary

The caller supplies only GitHub repository identity, issue number, and optional
authenticated read parameters. The caller cannot supply reward amount, reward
authority, evidence URL, currency conversion, or an as-of timestamp.

The gate reads the canonical issue before and after bounty_preflight. Existing
preflight already revalidates canonical issue/comment generation, assignment,
claim pressure, open-PR competition, stale-listing state, maintainer pauses,
credential safety, and advertised reward signals. Routing therefore inherits a
live source read rather than trusting caller-authored evidence fields.

The receipt hashes both the consumed issue generation and the complete safe
preflight result. Verification is intentionally not historical self-replay:
verify_live_cash_receipt performs a new live GitHub evaluation and requires the
current receipt to match exactly.

## Dollar routes

Production floors are captured privately by the module:

| fixed canonical USD reward | disposition |
| --- | --- |
| >= 50 | ACTIVE_REVIEW / main_bounty_queue |
| 10 through 49.99 | PILE_SAVE_UP / bounty_pile_10_49 |
| < 10 | PRUNE_BELOW_DOLLAR_FLOOR |
| no fixed USD | HOLD_NO_FIXED_USD_REWARD |

ACTIVE_REVIEW grants no claim, implementation, submission, outbound-contact,
payment, wallet, or revenue authority.

Native tokens are never converted to USD. Mixed USD/native-token reward
semantics hold instead of guessing economics.

## Fixed-amount semantics

A live source still cannot promote a ceiling as guaranteed cash. A canonical
reward line holds when it contains multiple dollar amounts or qualifiers such
as up to, at most, max/maximum, ceiling, range, between, variable,
depending-on, prize/reward/bounty pool, or milestone-total language.

Thus examples such as "up to $500" and "$10 - $500 bounty" cannot become a
$500 ACTIVE_REVIEW receipt.

## Relationship to older routers

This is the live source-authentication ancestor missing from request-driven
routers such as bounty_value_router and the donor material in PRs #411 and
#423. Those deterministic components can still project policy, but a
caller-authored FIRST_PARTY/amount assertion must not be treated as live bounty
admission authority.
