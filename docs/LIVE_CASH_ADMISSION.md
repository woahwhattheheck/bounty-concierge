# Live cash admission

bounty_live_cash_admission is the source-bound front door for new GitHub
bounty economics. It exists because a deterministic receipt is not trustworthy
when the same caller can supply reward amount, authority, evidence URL, and a
historical evaluation time.

## Trust boundary

The caller supplies GitHub repository identity, issue number, optional
authenticated read parameters, and an optional existing source-bound
`submission_target`. The caller cannot supply reward amount, reward authority,
reward evidence, currency conversion, or an as-of timestamp.

The gate reads the canonical issue before and after bounty_preflight. Existing
preflight already revalidates canonical issue/comment generation, assignment,
claim pressure, open-PR competition, stale-listing state, maintainer pauses,
credential safety, and advertised reward signals. Routing therefore inherits a
live source read rather than trusting caller-authored evidence fields.

The receipt hashes both the consumed issue generation and the complete safe
preflight result. Verification is intentionally not historical self-replay:
verify_live_cash_receipt performs a new live GitHub evaluation and requires the
current receipt to match exactly.

## Explicit delivery repository

For a bounty whose sponsor directs delivery to another repository, pass the
existing four-field `submission_target` record (`repository`, `source_url`,
`source_content_sha256`, and `instruction_excerpt`). Its source must identify
the canonical bounty issue or one of that issue's comments. The existing
validator checks this record before any provider read; no target is inferred.

Both live-cash APIs accept `submission_target=`. An evaluation with a target
adds only `source.submission_target_sha256` to its receipt, using SHA-256 of
the validated record's JSON with sorted keys, compact separators, and ASCII
escaping. The instruction excerpt is not copied into the receipt or summary.
`verify_live_cash_receipt` requires the same explicit target for that receipt;
a missing or different target returns false before a provider reread. Calls
without a target retain the existing receipt shape and verification behavior.

The direct CLI accepts the same explicit JSON file:

```bash
python -m concierge.bounty_live_cash_admission owner/bounties 17 \
  --submission-target submission-target.json --json
```

`revenue_dispatch.qualify_available_live_revenue_intake` and its CLI also accept
the target (`submission_target=` / `--submission-target`). The dispatcher
passes one validated copy to both initial revenue intake and the final
live-cash preflight, and requires the returned receipt's target digest to
match. Existing availability and economic receipt requirements still apply.
This preserves delivery-repository competition context throughout dispatch;
it does not create a claim, contribution, award, or payment.

## Dollar routes

Production floors are captured privately by the module:

| fixed canonical USD reward | disposition |
| --- | --- |
| >= 15 | ACTIVE_REVIEW / main_bounty_queue |
| 10 through 14.99 | PILE_SAVE_UP / bounty_pile_10_49 |
| < 10 | PRUNE_BELOW_DOLLAR_FLOOR |
| no fixed USD | HOLD_NO_FIXED_USD_REWARD |

The existing `bounty_pile_10_49` route identifier remains for compatibility;
the current active floor is $15 for work on green platforms, following the
owner's October 4 update. Existing payer and claim qualification still apply.

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
