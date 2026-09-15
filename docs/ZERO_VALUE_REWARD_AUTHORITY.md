# Zero-value reward authority

Paid-work dispatch requires a **positive** advertised native reward. A reward amount of exactly zero is evidence about the listing, but it is not evidence of paid work.

## Failure mode

The qualification parser intentionally accepts non-negative numeric reward syntax so receipts can preserve what the canonical listing says. Before this guard, downstream presence checks only asked whether a USD or RTC reward set was non-empty. That made exact zero look like an advertised reward:

```text
/bounty $0
reward: 0 RTC
label: $0
label: 0 RTC
```

With an otherwise clean canonical audit, a zero-valued listing could therefore reach `ACTIONABLE` and consume claim / implementation capacity despite offering no payout.

## Rule

`concierge.bounty_qualification.qualify_dispatch()` now emits:

```text
ZERO_VALUE_REWARD / HOLD
```

when **any** canonical advertised or live-label USD/RTC reward amount is exactly zero.

Zero stays in the normalized signal set. This is deliberate:

- an operator can distinguish “no reward was advertised” from “the listing explicitly advertises zero”;
- zero plus a positive amount still retains `AMBIGUOUS_ADVERTISED_REWARD`;
- zero disagreeing with another live/adverstised amount still retains `REWARD_MISMATCH`;
- source evidence is not silently rewritten into absence.

Decision precedence remains `REJECT > HOLD > ACTIONABLE`, so existing stronger rejection reasons continue to dominate.

## Native currencies

The rule does not perform FX conversion and does not compare USD against RTC. It asks only whether reward evidence in its native currency is strictly positive.

Examples:

| Evidence | Result |
| --- | --- |
| `/bounty $0` | HOLD |
| `$0` live label | HOLD |
| `reward: 0 RTC` | HOLD |
| `0 RTC` live label | HOLD |
| `/bounty $0.01` | eligible for the remaining gates |
| `reward: 1 RTC` | eligible for the remaining gates |

A positive amount is not sufficient by itself: canonical audit, source provenance, competition, staleness, private-context, mismatch, and other existing gates still apply.

## Revenue-intake composition

`concierge.revenue_intake.qualify_revenue_intake()` composes qualification and source-provenance gates. A zero-value qualification HOLD therefore appears as:

```text
QUALIFICATION:ZERO_VALUE_REWARD
```

and combined dispatch remains false. The integration regression exercises this real composition rather than testing only the lower-level parser.

## Authority ceiling

This guard is internal paid-work qualification only. It does not:

- claim a bounty;
- contact a sponsor or maintainer;
- submit work upstream;
- authorize provider mutation;
- assert acceptance or payout;
- recognize revenue or cash;
- assign an FX value to RTC;
- override a DNR or any outreach authority.

It answers one narrow authority question: **does the canonical reward evidence contain an explicit zero that makes this unsuitable for paid-work dispatch?**

## Regression suite

Run:

```bash
python -m unittest -v tests.test_zero_value_reward_authority
python -O -m unittest -v tests.test_zero_value_reward_authority
```

The focused suite covers zero USD in body/title/live-label form, zero RTC in body/title/live-label form, mixed zero/positive ambiguity, zero/live mismatch, positive USD and RTC controls, combined revenue-intake suppression, and safe output that does not echo the source body.

A dedicated GitHub Actions workflow executes the suite on Python 3.9 and 3.13 in normal and optimized modes. Hosted status must be reported separately from source review; absent or queued Actions are not green.
