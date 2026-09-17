# Certified settlement collections queue

`concierge.certified_settlement_collections_queue` is a read-only owner-review layer over two already-landed controls:

1. `reward_settlement_ledger` separates merge, advertised reward, award, eligibility, payout ticket, payout rail, transfer, terminal payment, and explicit no-reward closure.
2. `reward_settlement_certifier` independently binds those facts to the signed trusted-source registry.

The queue does **not** infer money from merge state. It only routes the strongest currently certified state into one of:

- `SETTLED`
- `CLOSED_NO_REWARD`
- `NEEDS_TRUST_EVIDENCE`
- `AWARD_FOLLOWUP_CANDIDATE`
- `PAYOUT_TICKET_FOLLOWUP_CANDIDATE`
- `PAYOUT_RAIL_FOLLOWUP_CANDIDATE`
- `TRANSFER_PENDING_FOLLOWUP_CANDIDATE`
- `HOLD_CONTRADICTION`

A declared downstream state that is ahead of its independently certified state is **not** treated as progress. It becomes `NEEDS_TRUST_EVIDENCE`. This makes an untrusted payout ticket/rail/transfer a request for better evidence rather than a reason to contact somebody.

## Currentness

The caller supplies an explicit canonical UTC `--as-of`; ambient wall-clock time is never authority. Non-terminal follow-up candidates older than `--max-evidence-age-seconds` are demoted to `NEEDS_TRUST_EVIDENCE`. Terminal certified payment and certified no-reward closure remain terminal facts even when old; age is still reported.

Each queue record carries `latest_certified_event_at` and `certified_event_age_seconds`. The queue receipt binds the normalized settlement input, ledger projection, trusted-registry body, certification receipt, optional contact-route evidence, and explicit `as_of`.

## Amounts and units

Advertised, awarded, and certified-paid facts remain distinct. Amounts stay in integer minor units with their original three-letter unit/currency. The queue performs no FX and no noncash-to-USD valuation. `recognized_revenue_by_currency` is intentionally empty.

## Optional contact-route evidence

Contact-route evidence is optional and is **reference-only**. A route is surfaced only when its `source_id`, `source_ref`, and `observed_at` exactly match a non-repository source in the signed trusted registry. Unknown, future, reminted/duplicate, or mismatched routes move the case to `HOLD_CONTRADICTION` and are not surfaced as usable routes.

A trust-bound route still does not authorize a send. It is only a reference an owner can inspect after the separate outbound/Muse collision controls run.

Contact input schema:

```json
{
  "schema": "bounty-concierge/settlement-contact-route-evidence/v1",
  "generated_at": "2026-09-17T00:30:00Z",
  "routes": [
    {
      "case_id": "case-1",
      "route_id": "route-1",
      "route_kind": "PROVIDER_TICKET",
      "source_id": "provider-route-source",
      "source_ref": "fixture://provider/route",
      "observed_at": "2026-09-17T00:10:00Z"
    }
  ]
}
```

Supported route kinds are `SPONSOR_ISSUE`, `SPONSOR_EMAIL`, `PROVIDER_TICKET`, `PAYOUT_PORTAL`, and `OTHER_REFERENCE`.

## CLI

The queue uses the same HMAC trust key as the sibling certifier and requires it from an environment variable. The default name is `REWARD_SETTLEMENT_TRUST_KEY`.

```bash
python -m concierge.certified_settlement_collections_queue \
  --input settlement-input.json \
  --registry trusted-registry.json \
  --as-of 2026-09-17T00:40:00Z \
  --queue collections-queue.json \
  --markdown collections-queue.md
```

Optional flags:

```bash
--contacts contact-routes.json
--max-evidence-age-seconds 604800
--key-env REWARD_SETTLEMENT_TRUST_KEY
```

Output files are create-exclusive and no-follow. A pre-existing file or output symlink fails closed.

## Authority ceiling

The queue is internal decision support only. All of the following remain hard-false in every output:

- send outbound email/comment/DM
- collect money
- request payout
- mutate sponsor/provider/wallet/bank state
- mutate accounting state
- recognize accounting revenue

`SETTLED` means independently certified payment evidence exists; it is not an instruction to book revenue. A follow-up candidate means “owner may review this evidence state,” not “contact this counterparty.”

## Verification

Run the focused suite in normal and optimized mode:

```bash
python -m py_compile concierge/certified_settlement_collections_queue.py tests/test_certified_settlement_collections_queue.py
python -m unittest -v tests.test_certified_settlement_collections_queue
python -O -m unittest -v tests.test_certified_settlement_collections_queue
python examples/certified_settlement_collections_queue_demo.py >/tmp/certified-settlement-queue-demo.json
```
