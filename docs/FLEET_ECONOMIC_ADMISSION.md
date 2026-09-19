# Fleet economic admission

`concierge.fleet_economic_admission` is the pre-dispatch economics fence for paid work.

The concierge already answers several different questions:

1. canonical intake / bounty qualification: **is there a current, explicit paid offer and is the work otherwise dispatchable?**
2. opportunity ranking / portfolio logic: **given explicit operator estimates, which qualified work ranks higher?**
3. closeout / settlement / realized economics: **what actually happened after work and provider evidence exist?**

None of those questions says that a nominally valid paid task is worth consuming a scarce premium-agent seat. A one-RTC task can be genuinely advertised, uncontested, technically appropriate, and still be economically irrational to work one at a time.

This module closes that seam without changing any of those existing authorities.

## Contract

The compiler accepts:

- the exact advertised reward in its **native currency**;
- an explicit operator estimate of agent-hours;
- an explicit policy for each accepted native currency;
- an optional operator-declared `batch_key` for work that is genuinely compatible enough to execute as one batch;
- one bounded canonical HTTPS source URL for each work identity.

It never accepts or invents a win probability. It never converts RTC to USD or produces a cross-currency winner.

A single opportunity is `SINGLE_ELIGIBLE` only when both are true:

- advertised reward >= `min_single_reward`;
- advertised reward / estimated agent-hours >= `min_reward_per_agent_hour`.

Anything that misses either single threshold is held unless it belongs to a same-currency batch. A batch is `BATCH_ELIGIBLE` only when all are true:

- item count is within the policy bounds;
- aggregate advertised reward >= `min_batch_reward`;
- aggregate advertised reward / aggregate estimated agent-hours >= the same currency's rate floor.

An individually eligible single is **removed from the batch pool before aggregation**. That prevents one large job from subsidizing a pile of otherwise uneconomic microtasks.

Duplicate `work_id`, duplicate canonical source identities, and one `batch_key` spanning multiple currencies fail the entire compile rather than silently double-counting or inventing FX.

## Canonical source identity fence

Economic aggregation occurs only after every source URL has passed a conservative identity compiler.

All source URLs must:

- use HTTPS;
- be at most 2,048 characters;
- contain a valid IDNA host;
- omit userinfo, query strings, fragments, explicit ports, trailing-dot hosts, and path dot segments.

GitHub issue URLs receive stronger binding because they are the primary bounty identity surface. The identity is:

```text
github-issue:<casefolded-owner>/<casefolded-repository>#<positive-issue-number>
```

This means host case, owner/repository case, `www.github.com`, an optional trailing slash, and leading zeroes in the issue number cannot create extra economic candidates. Encoded or repeated-separator GitHub paths fail closed.

For non-GitHub URLs, only the scheme/host are normalized; the path remains exact. The compiler deliberately does **not** guess that `/work/1` and `/work/1/` identify the same object on an arbitrary service.

The receipt binds the sorted normalized identities in `source_identity_sha256` and states `canonical_source_identity_rechecked=true`. Original source strings remain visible in candidate rows for auditability.

The byte-exact original v1 economics implementation is preserved privately as `concierge._fleet_economic_admission_v1`; the public module is the canonical-source-hardened wrapper. Existing callers retain the same public import and CLI path.

## Checked-in swarm policy

`policies/swarm_fleet_economics_v1.json` is an explicit planning policy, not a price oracle or accounting rule.

| Currency | Single floor | Compatible-batch floor | Native reward / agent-hour floor |
| --- | ---: | ---: | ---: |
| USD | 50 USD | 500 USD | 100 USD/hour |
| RTC | 200 RTC | 500 RTC | 300 RTC/hour |

The RTC shape is intentional: a 1-RTC one-hour task is held, while sufficiently large compatible batches can still make small repeated items worth processing together. The policy is content-addressed in every receipt; changing the thresholds requires changing the policy input rather than silently changing code.

For owner-directed new bounty labor, `concierge.paid_work_dollar_floor` runs before this generic compiler. It prevents verified USD cash below $10 from entering any batch at all, routes $10–49 to save-up inventory, and sends only $50+ items into ordinary downstream economics. The generic batch compiler intentionally remains reusable for other explicitly authorized workloads.\n\nThese numbers are dispatch-planning thresholds. They are **not** claims about fair wages, token value, market value, guaranteed bounty acceptance, expected payout, or realized revenue.

## Example

```json
{
  "schema": "fleet-economic-admission/v1",
  "policy": {
    "schema": "fleet-economic-policy/v1",
    "min_batch_items": 2,
    "max_batch_items": 200,
    "currencies": {
      "RTC": {
        "min_single_reward": "200",
        "min_batch_reward": "500",
        "min_reward_per_agent_hour": "300"
      }
    }
  },
  "candidates": [
    {
      "work_id": "elyan-123",
      "canonical_source_url": "https://github.com/example/repo/issues/123",
      "currency": "RTC",
      "advertised_reward": "1",
      "estimated_agent_hours": "1",
      "batch_key": null
    }
  ]
}
```

Run:

```bash
python -m concierge.fleet_economic_admission request.json --json
```

The candidate is returned as `ECONOMIC_HOLD` with the below-single and below-rate reasons. No provider mutation occurs.

For repeated work, use a common `batch_key` only when the work can actually share execution overhead. The compiler treats batch compatibility as an operator assertion; it does not inspect repositories or infer that unrelated tasks are compatible. Receipts state that they do not re-check canonical eligibility, so callers must AND this result with the live canonical intake/preflight result immediately before real dispatch.

## Authority ceiling

A green economics receipt means only:

> under the supplied native-currency policy and supplied effort estimate, this uniquely identified work is economically eligible for further dispatch review.

It does **not** mean:

- a GitHub issue is still open or uncontested;
- a maintainer approved a claim;
- external outreach is approved;
- work was submitted or accepted;
- a bounty was awarded;
- a payment settled;
- cash or revenue exists;
- an RTC/USD exchange rate exists;
- the batch key has independent compatibility authority.

The receipt therefore emits:

- `canonical_source_identity_rechecked=true`;
- `fx_conversion=false`;
- `win_probability_inferred=false`;
- `canonical_eligibility_rechecked=false`;
- `dispatch_authority=false`;
- `claim_or_submission_authority=false`;
- `payment_cash_or_revenue_authority=false`.

Use live canonical qualification/collision controls for eligibility and custody. Use provider-backed closeout/settlement surfaces for realized outcomes.

## Receipt integrity

The compiler canonicalizes the normalized policy and output, binds the policy SHA-256 and source-identity SHA-256, and emits a final `receipt_sha256`. `verify_receipt()` proves only self-integrity plus presence of the hardened source-identity authority marker. It cannot authenticate source reward data, effort estimates, or batch compatibility.

## Tests

```bash
python -m unittest -v \
  tests/test_fleet_economic_admission.py \
  tests/test_fleet_economic_source_identity.py
python -O -m unittest -v \
  tests/test_fleet_economic_admission.py \
  tests/test_fleet_economic_source_identity.py
python -m py_compile \
  concierge/_fleet_economic_admission_v1.py \
  concierge/fleet_economic_admission.py \
  tests/test_fleet_economic_admission.py \
  tests/test_fleet_economic_source_identity.py
```

Focused hostiles cover one-RTC microtasks, high-reward/low-rate work, aggregate batch floors, aggregate rate floors, anti-subsidy behavior, exact and aliased duplicate identities, mixed-currency batch keys, hostile URL ingress, float/bool/non-finite numeric ingress, missing policy, no cross-currency winner, deterministic receipts, tamper detection, and the CLI authority ceiling.


## Swarm bounty authority boundary

This compiler remains useful for generic native-currency economics, including
non-USD/noncash planning domains, but its receipt explicitly carries
`swarm_bounty_dispatch_authority=false`. A native-currency result such as
`SINGLE_ELIGIBLE` is therefore not enough to enter the swarm bounty claim or
implementation path.

New swarm bounty admission flows through `paid_work_dollar_floor` first and
only an ACTIVE dollar-floor ancestor can reach `paid_work_effort_value_gate`
GO. This separation preserves reusable economics while preventing token-only or
micro-bounty direct callers from bypassing the owner's $50 active floor.

