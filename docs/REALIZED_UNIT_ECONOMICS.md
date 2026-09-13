# Realized Unit Economics

`concierge.realized_unit_economics` answers one narrow operational question:

> For the exact paid-work scope being reviewed, how much wallet-evidenced RTC was
> realized per operator active hour?

It is a **decision-support** layer downstream of `concierge.revenue_settlement`.
It does not infer cash from merge state, advertised rewards, sponsor promises, or
self-authored settlement rows.

## Authority boundary

The compiler first calls the existing revenue-settlement boundary with:

- closeout items;
- exact operator payment bindings;
- canonical or captured wallet history;
- the recipient wallet and history provenance.

Only the `verified_amount` produced by that reconciliation becomes the cash
numerator. A fully paid item, a partial payment, and a zero-cash item therefore
remain distinguishable.

The effort denominator is deliberately weaker evidence. It is an
**operator-supplied active-minute log**. The receipt preserves that label and
never upgrades it to payroll, accounting, tax, or timekeeping authority.

Every reconciliation item must have exactly one effort row. Extra effort rows
and missing effort rows fail closed. This matters because excluding failed,
unpaid, or still-open work would mechanically inflate realized cash/hour.

## What it computes

For each item:

- exact wallet-evidenced RTC cash;
- operator active minutes;
- an RTC/hour estimate;
- settlement status;
- the payment-evidence fingerprints used by settlement.

For the complete supplied scope:

- total wallet-evidenced RTC;
- total active minutes, including zero-cash work;
- aggregate realized RTC/hour;
- counts of fully paid, partially paid, and zero-verified-cash items;
- a deterministic ranking by exact cash/minute ratio.

Ranking comparisons use exact cross multiplication, not floating-point
approximations. Displayed RTC/hour values use deterministic high-precision
decimal division.

The receipt also binds its normalized cash/effort scope with `scope_sha256` and
binds the full output with `receipt_sha256`. **That self-hash proves only
self-integrity. It is not an authenticated payment attestation.** Recomputing a
self-hash cannot create wallet authority; production cash authority comes from
running `compile_realized_unit_economics()`, which invokes
`revenue_settlement.reconcile_cash()` first.

## Effort schema

```json
{
  "schema_version": 1,
  "source": "operator_active_minutes",
  "items": [
    {
      "repo": "owner/repository",
      "pr": 123,
      "active_minutes": 90
    }
  ]
}
```

`active_minutes` is a strict integer from 1 through 525600. Booleans, decimal
minutes, strings, zero, negatives, and larger values are refused.

The CLI rejects duplicate JSON object keys and preserves JSON decimal values as
`Decimal` rather than binary floats.

## CLI

Use the same closeout, payment bindings, wallet and optional captured history
that you would use for settlement, plus the effort file:

```bash
python -m concierge.realized_unit_economics \
  closeout.json \
  bindings.json \
  effort.json \
  --wallet <wallet> \
  --history wallet-history.json
```

Omit `--history` to query the configured canonical wallet-history endpoint
through the settlement module.

Output is JSON only. The tool does not submit work, claim a bounty, contact a
sponsor, transfer tokens, mutate a wallet, or write accounting records.

## Important non-claims

A receipt explicitly carries:

- `fx_conversion: false`
- `accounting_revenue_claim: false`
- `tax_claim: false`
- `payout_or_transfer_authority: false`

No RTC-to-USD conversion is performed. The metric is native RTC per operator
active hour for the **exact supplied scope**, not a company-wide revenue,
profit, payroll, or tax figure.

## Failure model

The compiler fails closed when, among other cases:

- effort coverage is incomplete or has extra items;
- reconciliation identities are duplicated;
- a `verified_paid` row does not exactly match advertised RTC;
- a `partially_verified` row is zero, full, overpaid, or unmerged;
- a `not_inferred` row smuggles nonzero verified cash or payment evidence;
- one payment-evidence fingerprint is reused across economics items;
- currency is not RTC;
- money arrives as binary float;
- settlement summary totals disagree with item-level verified cash;
- history source or wallet shape is malformed.

The focused hostile suite also checks deterministic ranking, zero-cash
denominator inclusion, duplicate-key JSON rejection, exact decimal parsing,
scope-digest sensitivity, receipt tamper detection, and propagation of
settlement-layer evidence failures.
