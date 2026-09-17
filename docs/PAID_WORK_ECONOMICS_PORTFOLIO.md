# Paid-work economics portfolio replay

`concierge.paid_work_economics_portfolio` is an internal portfolio controller over the shipped paid-work effort/value gate (#216) and the enforced live implementation-dispatch seam (#220).

It does **not** discover opportunities, contact sponsors, claim work, submit work, mutate providers, request payouts, or recognize cash/revenue. It consumes a retained bounded inventory and answers one narrower question: which candidate generations still reproduce their exact economic gate state, and which verified `GO` rows are eligible to be handed to #220 for fresh live intake/availability revalidation?

## What it replays

Each retained candidate carries:

- a unique candidate generation ID;
- SHA-256 of the exact retained candidate payload;
- the full `paid_work_effort_value_gate` request;
- the full retained gate receipt.

The controller independently recompiles the gate request with `compile_paid_work_effort_value_gate`, requires exact semantic equality with the retained receipt, calls the gate receipt verifier, binds `work_id` and canonical source URL, checks the exact gate timestamp against the portfolio replay time, and rejects duplicate/replayed generation IDs, generation payloads, work IDs, or canonical-source aliases.

The top-level inventory must be complete and fresh within policy. A caller-supplied inventory generation digest is retained as custody metadata; candidate truth is independently bound by each exact candidate payload digest and gate replay.

## Exact partitions

The controller accepts only the four shipped #216 decisions:

- `GO`
- `HOLD_VALUE_UNKNOWN`
- `HOLD_ACCOUNT_GATE`
- `SKIP_ECONOMICS`

Reason codes are preserved. Non-GO rows receive a deterministic evidence/resolution worklist categorized into account/acceptance, valuation/economics, deadline, congestion, source/freshness, or other.

Only verified `GO` rows appear in `implementation_queue`. That queue contains identity and gate custody only. Every row says `requires_fresh_live_intake_and_availability=true` and points to `concierge.revenue_dispatch.qualify_available_live_revenue_intake`, the shipped #220 seam. `GO` here therefore means **eligible for existing live dispatch revalidation**, not implementation authority by itself and never external claim/submission authority.

## Five-minute synthetic demo

From the repository root:

```bash
python examples/paid_work_economics_portfolio_demo.py --out-dir /tmp/paid-work-portfolio-demo
cat /tmp/paid-work-portfolio-demo/portfolio.md
```

The demo creates four synthetic retained candidates that reproduce one row in each partition. The `GO` row remains internal eligibility only. All URLs use synthetic identities; no network access occurs.

Replay the exact generated input through the production CLI:

```bash
python -m concierge.paid_work_economics_portfolio compile \
  /tmp/paid-work-portfolio-demo/input.json \
  --out-dir /tmp/paid-work-portfolio-cli

python -m concierge.paid_work_economics_portfolio verify \
  /tmp/paid-work-portfolio-demo/input.json \
  --report /tmp/paid-work-portfolio-cli/portfolio.json \
  --markdown /tmp/paid-work-portfolio-cli/portfolio.md \
  --receipt /tmp/paid-work-portfolio-cli/receipt.json
```

Expected verifier output is `VERIFIED`. Output directories are create-exclusive.

## Failure modes

The focused hostile suite covers:

- gate receipt tamper;
- gate request / receipt drift;
- candidate-generation digest mismatch and replay;
- duplicate work-ID and canonical-source aliases;
- stale and future gate generations;
- incomplete or stale inventory;
- noncash valuation without authority;
- expired deadline skip behavior;
- report and bundle tamper;
- duplicate JSON keys and floating/non-finite JSON numbers;
- authority escalation attempts;
- create-exclusive CLI behavior.

Tests run normally and under `python -O`.

## Authority ceiling

All portfolio, partition-row, and queue external-authority fields remain false. The controller grants no sponsor contact, external claim/comment/submission, provider mutation, payment/wallet mutation, payout assertion, accounting-revenue recognition, cash receipt, or revenue claim. The #220 live dispatch seam must independently revalidate live intake and maintainer availability immediately before any internal implementation admission.