# Bounty Cash Close Board

`concierge.cash_close_board` is the composition layer between the repository's existing paid-work rails:

- live PR closeout / maintainer state,
- payout requests and escalation,
- RTC wallet settlement and settlement registry,
- receivables aging,
- collection-routing evidence.

It does **not** replace those authorities. It lets an operator normalize their receipts into one cross-surface queue without collapsing commercial states.

## State law

The board keeps `advertised`, `accepted`, `requested`, and `received` money separate. A merge or issue closure is delivery evidence, not acceptance or cash. A payment link is only a route. A sent request is only a request. A sponsor saying “paid” is an acknowledgment that should trigger payment-rail verification.

`SETTLED` is derived only when one or more `PAYMENT_RAIL_RECEIVED` events are bound to explicit `payment_rail` references and exactly cover a known obligation. The known obligation is an explicit accepted amount when present, otherwise the advertised amount. Partial receipts stay `PARTIALLY_SETTLED`; a receipt with no known obligation stays `PAYMENT_RECEIVED_UNPRICED`.

The tool never contacts anyone, initiates money movement, mutates a provider/wallet, or recognizes accounting revenue.

## Manifest

```json
{
  "schema_version": "bounty-cash-close-board/v1",
  "policy": {
    "schema": "bounty-cash-close-policy/v1",
    "followup_after_hours": 72,
    "escalate_after_hours": 168,
    "ack_verify_after_hours": 24
  },
  "claims": [
    {
      "claim_key": "upstream-project:issue-14",
      "owner": "settlement-owner",
      "advertised": {"amount": "250", "currency": "USD"},
      "references": [
        {"kind": "github_issue", "ref": "https://github.com/example/project/issues/14"},
        {"kind": "github_pr", "ref": "https://github.com/example/project/pull/41"}
      ],
      "events": [
        {
          "event_id": "delivery-41",
          "kind": "WORK_DELIVERED",
          "at": "2026-09-13T14:00:00Z",
          "source_ref": "https://github.com/example/project/pull/41",
          "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "amount": null
        },
        {
          "event_id": "review-gate-41",
          "kind": "EXTERNAL_REVIEW_REQUIRED",
          "at": "2026-09-13T14:05:00Z",
          "source_ref": "https://github.com/example/project/pull/41",
          "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "amount": null
        }
      ]
    }
  ]
}
```

Event evidence digests are globally single-use within a board. Event IDs are globally unique. Every event source must already be named in that claim's reference set. All event timestamps must be canonical UTC and cannot be in the verifier's future.

Supported event kinds:

- `WORK_DELIVERED`
- `WORK_MERGED`
- `WORK_ACCEPTED` (optional accepted amount)
- `EXTERNAL_REVIEW_REQUIRED`
- `PAYMENT_REQUEST_SENT` (amount required)
- `PAYMENT_REQUEST_FAILED` (amount required)
- `SPONSOR_PAID_ACK`
- `PAYMENT_RAIL_RECEIVED` (amount required; the only settlement-bearing event)

## Use

```bash
python -m concierge.cash_close_board close.json
python -m concierge.cash_close_board close.json --json
```

Markdown output is sorted by cash-close priority and includes SLA state. JSON output includes a deterministic receipt SHA-256, stage counts, per-currency advertised/unsettled totals, per-currency payment-rail receipts, and per-currency settled totals. There is intentionally no FX aggregation.

## Recommended composition

Generate evidence from the existing authoritative tools first. For example, use live closeout evidence to support `WORK_MERGED` / maintainer state, settlement-registry or revenue-settlement evidence to support `PAYMENT_RAIL_RECEIVED`, and collection-routing receipts to support a payment request. Hash the exact retained source artifact and use that digest as the event `sha256`; do not replace the underlying proof with prose.
