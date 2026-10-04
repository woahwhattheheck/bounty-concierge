# Certified settlement collection queue

The landed `concierge.certified_settlement_collection_queue` composes the reward-settlement ledger and signed trust certificate into a deterministic, read-only owner-review queue. `concierge.certified_settlement_collection_review_packet` is the terminal operator layer: it verifies the queue receipt, binds separately observed contact-route evidence, and emits a reasoned next-action packet without granting send, payout, provider, wallet, bank, or accounting authority.

## Truth boundary

The queue is downstream of merged-work evidence, not a substitute for it. Merge is not payment. An advertised bounty is not an award. A payout ticket or payout rail is not a transfer. A pending or confirming transfer is not paid. Currency and amount remain in the source unit (`amount_minor` plus the original currency); this layer performs no FX conversion and does not value noncash rewards as USD.

The core queue partitions each case into exactly one of these states:

- `SETTLED`: independently certified confirmed incoming transfers cover the certified sponsor award in the same currency.
- `CLOSED_NO_REWARD`: independently certified terminal no-reward closure.
- `NEEDS_TRUST_EVIDENCE`: the latest declared state is not independently certified/current enough for a follow-up decision.
- `AWARD_FOLLOWUP_CANDIDATE`: a sponsor award is certified but no payout ticket is certified.
- `PAYOUT_TICKET_FOLLOWUP_CANDIDATE`: a payout ticket is certified but no payout rail is certified.
- `PAYOUT_RAIL_FOLLOWUP_CANDIDATE`: a payout rail is certified but no transfer evidence is certified.
- `TRANSFER_PENDING_FOLLOWUP_CANDIDATE`: certified transfer evidence remains nonterminal.
- `PARTIAL_PAYMENT_FOLLOWUP_CANDIDATE`: confirmed incoming payments do not yet cover the certified sponsor award in its own currency.
- `HOLD_CONTRADICTION`: certified/declared settlement facts conflict or a certified transfer is terminal-failed in a way that requires review.

Full certified payment and no-reward closure do not become collection work merely because time passes. Nonterminal follow-up states, including payment shortfalls, are currentness-gated by the explicit `as_of` and `freshness_seconds` policy inputs already bound into the core queue.

Each row's `award_balance` compares the certified sponsor award with the sum of certified confirmed incoming payments in that currency. It contains `award_minor`, `paid_minor`, `remaining_minor`, `currency`, `unit`, and the award `source_id`. For example, a 9,000-minor-unit USD award with 100 received retains 8,900 outstanding and a payment follow-up. Multiple confirmed transfers add together; pending, failed, outgoing, and other-currency transfers do not reduce that balance. The separate `certified_paid_by_currency` field preserves every actually received currency amount. No FX rate, fee allowance, or refund instruction is inferred.

Without a certified sponsor award, `award_balance` is `null` and a confirmed receipt remains `NEEDS_TRUST_EVIDENCE` for payment completeness. An advertised amount alone never establishes an outstanding balance. The source ledger and certificate continue to record the actual received money; a terminal transfer alone no longer closes collection for an unpaid award.

## Separately observed contact routes

Contact routes are optional evidence. They do **not** authorize a message. The review-packet overlay requires an exact document:

```json
{
  "schema": "bounty-concierge/certified-settlement-contact-routes/v1",
  "as_of": "2026-09-17T01:00:00Z",
  "routes": []
}
```

Each route has exact keys:

```json
{
  "case_id": "case-1",
  "route_id": "route-1",
  "kind": "EMAIL",
  "route_ref": "opaque-route-reference",
  "disposition": "AVAILABLE",
  "source_ref": "evidence://route-source",
  "source_sha256": "<64 lowercase hex>",
  "observed_at": "2026-09-17T00:55:00Z"
}
```

Supported kinds are `EMAIL`, `ISSUE`, `TICKET`, `DM`, `FORM`, and `OTHER`. Dispositions are `AVAILABLE`, `DNR`, and `UNKNOWN`. The route document `as_of` must exactly match the certified queue `as_of`; future observations, duplicate route identities, unknown case IDs, duplicate JSON keys, invalid hashes, and malformed timestamps fail closed.

A case with both `AVAILABLE` and `DNR` route evidence is held as `HOLD_CONFLICT`. A DNR route on a follow-up candidate yields `HOLD_DNR`. Missing or unknown route evidence yields `FIND_ROUTE`, not permission to contact anyone.

## Reasoned next-action packet

`build_review_packet(queue, routes)` first verifies the core queue's deterministic `receipt_sha256` and all-false authority ceiling. It then preserves the queue state, reason codes, money basis, and event age while adding route evidence and a deterministic next action:

- `SETTLED` / `CLOSED_NO_REWARD` -> `NO_FOLLOWUP`
- `NEEDS_TRUST_EVIDENCE` -> `REFRESH_EVIDENCE`
- `HOLD_CONTRADICTION` or conflicting route evidence -> `HOLD`
- DNR on a follow-up candidate -> `HOLD_DNR`
- no usable route on a follow-up candidate -> `FIND_ROUTE`
- award candidate -> `REVIEW_AWARD_STATUS`
- payout-ticket candidate -> `REVIEW_PAYOUT_TICKET`
- payout-rail candidate -> `REVIEW_PAYOUT_RAIL`
- transfer-pending candidate -> `REVIEW_TRANSFER_STATUS`
- partial-payment candidate -> `REVIEW_REMAINING_PAYMENT`

The review packet retains `award_balance` and `certified_paid_by_currency`. The accepted-work bridge also retains both amounts and routes an explicitly accepted partial-payment case to `ACCEPTED_UNPAID_COLLECTIONS_REVIEW`, rather than `PAID_CLOSED`. Existing missing-route, DNR, contradiction, acceptance-evidence, and freshness handling still applies.

The packet binds the exact queue receipt and canonical contact-route document hash, and then emits its own deterministic receipt. `outbound_authorized` is always false per case, and every mutation/outbound/accounting authority flag is hard false globally.

## Usage

```python
from concierge.certified_settlement_collection_review_packet import (
    ROUTES_SCHEMA,
    build_review_packet,
)

routes = {"schema": ROUTES_SCHEMA, "as_of": queue["as_of"], "routes": []}
packet = build_review_packet(queue, routes)
```

For strict byte-oriented callers, `compile_bytes(queue_raw, routes_raw)` rejects duplicate JSON keys and emits canonical JSON plus a final newline.

Run focused proof with:

```bash
python -m unittest -v \
  tests.test_certified_settlement_collection_queue \
  tests.test_certified_settlement_collection_review_packet
python -O -m unittest -v \
  tests.test_certified_settlement_collection_queue \
  tests.test_certified_settlement_collection_review_packet
python examples/certified_settlement_collection_review_packet_demo.py
```

## Authority ceiling and Muse

These artifacts are review/evidence infrastructure only. They cannot send email, Slack/DM, issue comments, or forms; they cannot request payout; and they cannot mutate providers, wallets, banks, or accounting revenue. If an owner later chooses to perform outbound contact, the fleet's separate single-writer/Muse arbitration and provider-send controls still apply. Route availability in this packet is evidence only, never Muse clearance or send authority.
