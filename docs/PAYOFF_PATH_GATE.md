# Payoff Path Gate

`concierge.payoff_path_gate` is an offline owner-review product for one commercial question:

> **Before compensation is secured, is there a current, evidence-backed route from this unpaid/speculative work to a possible payoff, and is our free-work exposure still inside an owner-set cap?**

It exists to prevent indefinite free implementation, speculative competition work with no usable prize/entry path, and “relationship building” whose conversion step was never named. It is deliberately **not** an expected-value engine and deliberately **not** a buyer-facing sales tool.

## What counts as a payoff path

A work item either has no payoff path (`null`) or binds all of the following:

- one mechanism: `BOUNTY`, `COMPETITION_PRIZE`, `PAID_OFFER_OR_PILOT`, `PRIME_SUBCONTRACT`, `REFERRAL_COMMISSION`, or `SPONSOR_OR_GRANT`;
- a source value state:
  - `FIXED` or `POOL` requires exact positive integer minor units plus a 3-letter currency;
  - `NEGOTIATED` or `UNSPECIFIED_BY_SOURCE` **must not** carry a made-up amount;
- canonical HTTPS source evidence with immutable evidence ref/SHA-256, observed-at time, and owner-selected freshness bound;
- the mechanism-specific next conversion event and deadline, with separate evidence ref/SHA-256;
- positive owner-set free-work budget in minutes and exact minutes already spent.

Mechanism-to-event mappings are fixed so “have a plan” cannot degrade into vague prose:

| Mechanism | Required next conversion event |
|---|---|
| `BOUNTY` | `SUBMIT_WORK` |
| `COMPETITION_PRIZE` | `ENTER_COMPETITION` |
| `PAID_OFFER_OR_PILOT` | `SEND_PAID_OFFER` |
| `PRIME_SUBCONTRACT` | `SECURE_TEAMING` |
| `REFERRAL_COMMISSION` | `COMPLETE_REFERRAL` |
| `SPONSOR_OR_GRANT` | `APPLY_FOR_GRANT` |

## States

- `READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW`: path evidence is current, deadline is still open, and unpaid exposure is below the cap. **Review only.**
- `HOLD_NO_PAYOFF_PATH`: no compensation path was evidenced.
- `HOLD_STALE_OR_INVALID`: path evidence is stale/future, conversion deadline is expired/invalid, or work start time is future.
- `STOP_UNPAID_WORK`: already-spent unpaid minutes are greater than or equal to the owner-set cap. This is strongest even when the path is otherwise valid.

No state means “won,” “accepted,” “paid,” “revenue,” or permission to contact/submit/enter/spend.

## Input example

```json
{
  "schema": "payoff-path-work/v1",
  "work_items": [
    {
      "work_id": "fix-384",
      "opportunity_id": "agentlily-267",
      "started_at_utc": "2026-09-13T13:00:00.000Z",
      "free_work_budget_minutes": 240,
      "free_work_spent_minutes": 90,
      "payoff_path": {
        "mechanism": "BOUNTY",
        "value": {"kind": "FIXED", "currency": "USD", "amount_minor": 9000},
        "source": {
          "canonical_url": "https://example.com/bounty/267",
          "evidence_ref": "terms:v3",
          "evidence_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "observed_at_utc": "2026-09-13T12:00:00.000Z",
          "max_age_days": 7
        },
        "conversion": {
          "event": "SUBMIT_WORK",
          "due_at_utc": "2026-09-20T12:00:00.000Z",
          "evidence_ref": "deadline:v1",
          "evidence_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        }
      }
    }
  ]
}
```

## CLI

Production compilation deliberately has **no caller-supplied `--as-of` flag**. Current process UTC is the authority.

```bash
python -m concierge.payoff_path_gate compile \
  --input work.json \
  --packet payoff.packet.json \
  --markdown payoff.review.md \
  --receipt payoff.receipt.json

python -m concierge.payoff_path_gate verify \
  --input work.json \
  --packet payoff.packet.json \
  --markdown payoff.review.md \
  --receipt payoff.receipt.json
```

Outputs are create-exclusive. Final symlink outputs, existing outputs, symlink/FIFO/non-regular inputs, duplicate JSON keys, unknown fields, bool-as-int values, unsafe integers, noncanonical UTC, malformed SHA-256, secret/PII-shaped durable refs, and non-HTTPS source URLs fail closed.

## Verification model

The packet is bound to a canonical digest of the normalized input. The receipt binds the input digest, exact packet digest, and exact Markdown bytes. Verification:

1. re-normalizes exact inputs;
2. rejects a packet evaluated in the future;
3. recompiles at the packet's original trusted process timestamp and requires exact packet/Markdown/receipt equality;
4. re-evaluates temporal validity at the verifier's **current process UTC**;
5. rejects a previously-READY item if its source/deadline has since gone stale.

This preserves reproducibility without allowing an old READY packet to act like current authority forever.

## Authority ceiling

The gate performs offline owner decision support only. It cannot authorize or perform buyer/platform/prime contact, email/SMS/DM/calls, supplier registration, competition entry, bid/proposal/work submission, contract/signature action, source work outside its own carrier, delivery/fulfillment, invoice/payment/refund, bank/provider mutation, spend/deployment, tax/accounting/legal determinations, award/payment/cash assertions, or recognized-revenue claims.
