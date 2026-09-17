# Certified settlement collections queue

`concierge.certified_settlement_collections_queue` turns the existing reward-settlement evidence stack into an **internal owner-review queue**. It composes, rather than replaces:

1. `reward_settlement_ledger.py` — strict declared-evidence reconciliation.
2. `reward_settlement_certifier.py` — independent trusted-source certification against a signed registry.
3. This queue — next-evidence routing only after the certificate is re-verified.

## Truth boundary

The queue never infers a receivable or payment from repository merge state. An advertised bounty is not an award. An award is not a payout ticket. A ticket or rail is not a transfer. A non-terminal transfer is not payment. Noncash units such as `RTC` remain their original unit and are never silently valued in USD.

Terminal `SETTLED` requires the upstream certifier to reproduce `PAID_CERTIFIED` from a signed trusted-source registry and a confirmed incoming transfer. `CLOSED_NO_REWARD` likewise requires independently certified closure evidence. The queue still reports `recognized_revenue_by_currency: {}` and grants no accounting authority.

## Queue states

- `SETTLED` — terminal confirmed incoming payment is independently certified.
- `CLOSED_NO_REWARD` — terminal no-reward closure is independently certified.
- `NEEDS_TRUST_EVIDENCE` — work or a stronger declared settlement fact is not independently certified, the nonterminal payout chain lacks a certified sponsor award, or the bound input/registry generation is stale under the explicit queue policy.
- `AWARD_FOLLOWUP_CANDIDATE` — a current advertised reward is certified and award evidence is absent.
- `PAYOUT_TICKET_FOLLOWUP_CANDIDATE` — sponsor award is certified and ticket evidence is absent.
- `PAYOUT_RAIL_FOLLOWUP_CANDIDATE` — payout-ticket evidence plus sponsor award are certified and rail evidence is absent.
- `TRANSFER_PENDING_FOLLOWUP_CANDIDATE` — payout-rail/non-terminal transfer evidence plus sponsor award are certified and terminal payment evidence is absent.
- `HOLD_CONTRADICTION` — independently certified facts conflict with safe queue routing (for example certified ineligibility paired with a reward follow-up).

Every state is owner-review only. No state authorizes an email, issue comment, form submission, payout request, provider mutation, wallet/bank mutation, or accounting entry.

## Currentness

The CLI requires an explicit `--as-of` and `--max-evidence-age-seconds`; it never reads wall-clock time. Both the settlement-input generation and trusted-registry generation must be within that policy window for a non-terminal follow-up. Terminal certified payment/closure remains historical terminal evidence even if the queue is later rerun outside the currentness window; the record still exposes `source_current: false` for review.

`age_since_last_certified_provider_event_seconds` is informational and is computed only from event sources actually included in the verified certificate and whose authority is `SPONSOR`, `PROVIDER`, `WALLET`, or `BANK`.

## Optional contact-route evidence

A separate `bounty-concierge/settlement-contact-routes/v1` file may list source-bound route observations. It is never treated as permission to contact the counterparty. Current/stale/missing route evidence is reported separately; `route_presence_grants_send_authority` is always false. Any live outbound action still requires the separate last-inch DNR/collision/Muse single-writer process.

Example route file:

```json
{
  "schema": "bounty-concierge/settlement-contact-routes/v1",
  "generated_at": "2026-09-16T12:00:00Z",
  "routes": [
    {
      "case_id": "example-1",
      "route_id": "example-1-sponsor-email",
      "channel": "EMAIL",
      "target_ref": "sponsor@example.invalid",
      "source_ref": "retained:route-observation-1",
      "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "observed_at": "2026-09-16T12:00:00Z",
      "valid_until": null
    }
  ]
}
```

## Compile and verify

The same trust key used by `reward_settlement_certifier.py` is read from `REWARD_SETTLEMENT_TRUST_KEY` by default. The key is never serialized into the queue or receipt.

```bash
export REWARD_SETTLEMENT_TRUST_KEY='...controlled runtime secret...'
python -m concierge.certified_settlement_collections_queue compile \
  --input settlement-input.json \
  --registry trusted-sources.json \
  --certificate settlement-certificate.json \
  --contact-routes contact-routes.json \
  --as-of 2026-09-16T13:00:00Z \
  --max-evidence-age-seconds 86400 \
  --out-dir /tmp/collections-queue

python -m concierge.certified_settlement_collections_queue verify \
  --input settlement-input.json \
  --registry trusted-sources.json \
  --certificate settlement-certificate.json \
  --contact-routes contact-routes.json \
  --as-of 2026-09-16T13:00:00Z \
  --max-evidence-age-seconds 86400 \
  --queue /tmp/collections-queue/queue.json \
  --markdown /tmp/collections-queue/queue.md \
  --receipt /tmp/collections-queue/receipt.json
```

The queue and receipt bind the exact SHA-256 of the settlement input, signed registry, certificate, and optional contact-route bytes. Verification recomputes all artifacts byte-for-byte.