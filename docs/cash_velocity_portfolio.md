# Cash-velocity portfolio allocation

`concierge.cash_velocity_portfolio` composes three already-separated authority layers without turning any of them into payment or outreach authority:

1. **Canonical opportunity intake/ranking** establishes which advertised USD opportunities are eligible for decision support.
2. **Live cash calibration** reacquires GitHub closeout state and canonical wallet history, then may only lower the operator's same-repository win probability from terminal cash observations.
3. **Cash-cycle review** measures repository-level merge-to-confirmed-transfer lag from an evidence-bound historical receipt.

The velocity allocator adds one new constraint: a repository whose verified cash-cycle state is `READY_FOR_OWNER_CASH_CYCLE_REVIEW` may consume at most an operator-selected fraction of total engineering capacity. The exact bounded solver then maximizes the same estimated USD expected value subject to total capacity, deadlines, collision groups, and those repository caps.

This is a **concentration-control mechanism**, not a currency model. It never converts RTC to USD and never changes advertised reward amounts.

## Why this exists

The prior layers can correctly conclude all of the following at once:

- an opportunity is currently actionable;
- its expected value per engineering hour is high;
- historical same-repository work has sometimes produced confirmed cash evidence;
- confirmed transfers from that repository have nevertheless arrived slowly.

Without a composition layer, an exact EV optimizer can still concentrate most scarce engineering time into that slow-to-cash repository. The velocity cap makes the tradeoff explicit and deterministic instead of relying on an operator to manually re-rank work after reading a separate lag report.

## Velocity policy

The request carries exactly:

```json
{
  "schema_version": 1,
  "max_receipt_age_hours": 24,
  "slow_repo_max_capacity_fraction": "0.50"
}
```

- `max_receipt_age_hours` bounds how old the verified cash-cycle receipt may be at allocation time.
- `slow_repo_max_capacity_fraction` is an exact decimal in `[0, 1]`. It is multiplied by total portfolio capacity to produce a per-repository effort cap for repositories in `READY_FOR_OWNER_CASH_CYCLE_REVIEW`.
- A value of `1` preserves the ordinary total-capacity bound for slow repositories; `0` excludes their work from this allocation generation.
- Repositories with `INSUFFICIENT_CONFIRMED_HISTORY`, `OBSERVED_NO_REVIEW_TRIGGER`, or no matching cash-cycle row are **not** penalized. Missing evidence is not fabricated into a negative signal.

## Evidence and freshness requirements

Before the lag state can affect selection, the allocator:

1. validates the cash-cycle receipt schema, SHA-256 shape, and non-authoritative safety fields;
2. rejects verifier-time rollback and future receipts;
3. enforces `max_receipt_age_hours`;
4. recomputes `verify_cash_cycle_review(...)` against the exact closeout, wallet-history, binding, and cash-cycle policy payloads supplied with the receipt;
5. rejects duplicate repository identities;
6. independently runs `allocate_cash_calibrated_portfolio(...)`, which reacquires live GitHub closeout and canonical wallet history for probability calibration;
7. rejects any cash-calibration result that claims currency conversion, cash/settlement authority, claim/submission authority, or probability increases.

The two evidence families intentionally answer different questions. Live calibration controls *probability*. Historical transfer-lag evidence controls only *capacity concentration*.

## Exact selection semantics

The solver is bounded to at most 20 feasible ranked candidates, matching the canonical exact portfolio allocator's bounded-search contract. It preserves:

- total capacity;
- earliest-deadline feasibility;
- one selection per collision group;
- exact expected-value maximization;
- the canonical tie-break order: higher expected value, then lower capacity consumption, then higher skill sum, then lexical source order.

For every slow repository it also enforces:

```text
sum(selected effort for repo) <= total capacity * slow_repo_max_capacity_fraction
```

A candidate that cannot fit under its repository cap by itself is explicitly excluded as `EXCEEDS_REPO_VELOCITY_CAP`. Other non-selected feasible candidates use `NOT_IN_MAX_CASH_VELOCITY_PORTFOLIO`.

## CLI

```bash
python -m concierge.cash_velocity_portfolio \
  request.json \
  closeout_manifest.json \
  payment_bindings.json \
  effort.json \
  cash_cycle_receipt.json \
  cash_cycle_closeout.json \
  cash_cycle_history.json \
  cash_cycle_bindings.json \
  cash_cycle_policy.json \
  --wallet "$RTC_WALLET" \
  --summary
```

`request.json` contains `candidates`, `skills`, `capacity_hours`, and `velocity_policy`.

At most one JSON input may be `-` (stdin). The inherited live-calibration file reader retains bounded strict UTF-8 JSON and regular-file custody semantics.

## Authority ceiling

A velocity receipt does **not** establish or authorize:

- payout, settlement, debt, receivables, cash, accounting revenue, or tax treatment;
- sponsor or payer identity;
- sponsor/maintainer contact;
- bounty claim or submission;
- wallet or provider mutation;
- RTC/USD or any other currency conversion.

Confirmed transfer lag is used only as a verified historical timing signal for an operator-selected capacity concentration limit. Advertised USD remains advertised USD, and RTC never enters USD arithmetic.
