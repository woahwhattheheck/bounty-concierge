# Reward Settlement Ledger

`concierge.reward_settlement_ledger` is the evidence-state layer above the existing cash reconciler. It answers a narrower question than revenue accounting: **what has an identified sponsor/provider source actually proved about this work's reward lifecycle?**

A merged pull request is deliberately not a settlement event. The ledger never turns merge, approval, a public bounty amount, a ticket, or a payment address into cash or recognized revenue.

## States

The ledger can retain explicit evidence for `ELIGIBILITY_UNKNOWN`, `ADVERTISED`, `AWARDED`, `TICKET_OPENED`, `RAIL_SUPPLIED`, `TRANSFER_EVIDENCED`, `PAID`, and `CLOSED_WITHOUT_REWARD`. Each state has one allowed evidence class: sponsor publication, sponsor decision, payout ticket, payment rail, transfer evidence, verified cash, or sponsor closeout. A record may legitimately skip intermediate evidence; `evidence_gaps` reports the missing facts instead of inventing them.

`PAID` requires a `verified_cash` event with `cash_status=verified_paid`. For RustChain/RTC work, use the existing `revenue_settlement` wallet-history reconciler first and pass only its `verified_paid` output through `paid_event_from_reconciliation`. `partially_verified` and `not_inferred` are rejected.

## Amounts and units

Amounts always carry `{kind, code, amount}`. `kind=currency` represents a currency code such as `USD` or `RTC`; `kind=unit` represents non-cash units such as points or credits. The compiler performs **no currency conversion and no unit-to-cash conversion**. Advertised, awarded, and paid remain separate claims. If advertised/awarded or awarded/paid denominations differ, compilation fails closed until a separate explicit conversion-evidence design is added.

## CLI

```bash
python -m concierge.reward_settlement_ledger compile evidence.json -o ledger.json
python -m concierge.reward_settlement_ledger verify ledger.json
```

Input:

```json
{
  "schema_version": 1,
  "items": [
    {
      "work_id": "reference/example#42",
      "repo": "example/project",
      "pr": 42,
      "events": [
        {
          "kind": "ADVERTISED",
          "source_type": "sponsor_publication",
          "source_ref": "https://example.invalid/bounty/42",
          "source_sha256": "<sha256 of retained source bytes>",
          "observed_at": "2026-09-16T20:00:00Z",
          "denomination": {"kind": "currency", "code": "USD", "amount": "90"}
        }
      ]
    }
  ]
}
```

The compiled document retains normalized evidence, derived state, evidence gaps, separate advertised/awarded/paid claims, and a canonical `receipt_sha256`. `verify` reconstructs semantics from the retained events and rejects tampering.

## Authority boundaries

- GitHub merge/approval is delivery evidence only; it cannot mint `AWARDED` or `PAID`.
- Public bounty text proves `ADVERTISED`, not sponsor award/eligibility.
- A support/payout ticket proves `TICKET_OPENED`, not award or cash.
- A payment address or rail proves `RAIL_SUPPLIED`, not transfer.
- A confirmed transfer may prove `TRANSFER_EVIDENCED`; it is still distinct from the ledger's verified-cash `PAID` event.
- `PAID` is cash evidence, **not revenue-recognition accounting**. `revenue_recognition` is always `not_computed`.
- Synthetic/reference examples must remain labeled as such outside the compiler; the ledger only validates the evidence contract supplied to it.
