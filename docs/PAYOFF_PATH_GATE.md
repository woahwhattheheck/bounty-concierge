# Payoff Path Gate

`concierge.payoff_path_gate` is an offline owner-review control for one commercial question:

> **Before compensation is secured, is there a current, evidence-backed path from speculative work to a possible payoff, and is unpaid exposure still inside explicit owner policy?**

The gate is deliberately not an expected-value engine, buyer-facing sales tool, bounty submitter, payment system, or revenue-recognition surface.

## Production schemas

### `payoff-path-work/v3` — evidence-bound effort + owner-policy continuity

V3 is the preferred surface. Its `continuity` object uses `payoff-path-continuity/v2` and contains an append-only event ledger.

Each event has the exact fields:

- `event_id`
- `kind`: `BUDGET_POLICY` or `EFFORT`
- `opportunity_id`
- `work_id` (`null` for `BUDGET_POLICY`)
- `minutes`
- `occurred_at_utc`
- `evidence_ref`
- `evidence_sha256`
- `policy_generation` (integer for `BUDGET_POLICY`, otherwise `null`)
- `predecessor_policy_sha256` (required for policy generations after 0)

`EFFORT` is therefore not a caller-authored scalar. Every admitted effort fact is bound to immutable evidence and exact work/opportunity identity. Exact byte-identical replay of an event ID is idempotent; replaying the same ID with changed content fails closed.

`BUDGET_POLICY` is versioned owner authority. Generation 0 establishes the initial cap. A successor policy must advance by exactly one generation and name the canonical SHA-256 of its predecessor policy. This makes cap changes visible rather than allowing a silent rewrite.

An owner may tighten a cap. An explicit successor policy may also raise a cap after a prior `STOP_UNPAID_WORK`; when that actually reopens work, the packet exposes `OWNER_POLICY_SUPERSESSION_REOPENED_AFTER_STOP`. A reset, omission, same-generation mutation, policy fork, predecessor mismatch, or transplanted receipt cannot reopen work.

V3 receipts bind:

- exact normalized input digest;
- exact packet and Markdown bytes;
- ledger identity and generation;
- exact previous receipt digest for nonzero generations;
- prior event count + canonical prefix root;
- immutable work/opportunity scope digest;
- current owner-policy heads, including policy generation, cap, evidence ref/SHA, policy digest, predecessor digest, and visible reopen state.

Work-item `free_work_budget_minutes` and `free_work_spent_minutes` remain redundant assertions for operator readability. They must equal the current policy head and cumulative admitted effort or compilation fails closed.

### `payoff-path-work/v2` — landed continuity compatibility

The landed v2 append-only continuity protocol remains supported. It binds ledger ID, generation+1, previous receipt digest, event count/root and cumulative effort so packet-local reset attacks remain closed. V3 is the stronger surface when evidence-bound effort and versioned budget-policy supersession are required.

### `payoff-path-work/v1` — migration only

V1 snapshot documents are not production READY authority. Normal `compile_gate(...)` and the CLI fail them closed even when a caller supplies deterministic trusted time in library code.

Historical/test replay that must reproduce old v1 READY semantics is intentionally separated behind the explicit migration APIs:

```python
from concierge.payoff_path_gate import (
    compile_legacy_migration_gate,
    verify_legacy_migration_gate,
)
```

Those names are the authority boundary: migration replay is not a production recommendation.

## Payoff path evidence

A work item either has no payoff path (`null`) or binds all of the following:

- mechanism: `BOUNTY`, `COMPETITION_PRIZE`, `PAID_OFFER_OR_PILOT`, `PRIME_SUBCONTRACT`, `REFERRAL_COMMISSION`, or `SPONSOR_OR_GRANT`;
- source value state:
  - `FIXED` or `POOL` requires positive integer minor units plus a 3-letter currency;
  - `NEGOTIATED` or `UNSPECIFIED_BY_SOURCE` must not carry an invented amount;
- canonical HTTPS source evidence with immutable evidence ref/SHA-256, observed-at time, and owner-selected freshness bound;
- mechanism-specific next conversion event and deadline with separate evidence ref/SHA-256.

| Mechanism | Required next conversion event |
|---|---|
| `BOUNTY` | `SUBMIT_WORK` |
| `COMPETITION_PRIZE` | `ENTER_COMPETITION` |
| `PAID_OFFER_OR_PILOT` | `SEND_PAID_OFFER` |
| `PRIME_SUBCONTRACT` | `SECURE_TEAMING` |
| `REFERRAL_COMMISSION` | `COMPLETE_REFERRAL` |
| `SPONSOR_OR_GRANT` | `APPLY_FOR_GRANT` |

## States

- `READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW`: path evidence is current, deadline is open, and unpaid exposure is below the current owner cap. **Review only.**
- `HOLD_NO_PAYOFF_PATH`: no compensation path is evidenced.
- `HOLD_STALE_OR_INVALID`: evidence/time/continuity is not current enough to support READY.
- `STOP_UNPAID_WORK`: cumulative unpaid effort is greater than or equal to the current owner cap.

No state means won, accepted, paid, cash received, recognized revenue, or permission to contact, submit, enter, spend, deliver, or mutate an external provider.

## V3 input sketch

A complete payoff-path object is omitted here for brevity; the continuity portion looks like:

```json
{
  "schema": "payoff-path-work/v3",
  "continuity": {
    "schema": "payoff-path-continuity/v2",
    "ledger_id": "owner-free-work-ledger-v3",
    "generation": 0,
    "previous_receipt_sha256": null,
    "events": [
      {
        "event_id": "policy-0",
        "kind": "BUDGET_POLICY",
        "opportunity_id": "agentlily-267",
        "work_id": null,
        "minutes": 240,
        "occurred_at_utc": "2026-09-13T13:00:00.000Z",
        "evidence_ref": "owner-policy:0",
        "evidence_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "policy_generation": 0,
        "predecessor_policy_sha256": null
      },
      {
        "event_id": "effort-0",
        "kind": "EFFORT",
        "opportunity_id": "agentlily-267",
        "work_id": "fix-384",
        "minutes": 90,
        "occurred_at_utc": "2026-09-13T14:00:00.000Z",
        "evidence_ref": "effort-receipt:fix-384-0",
        "evidence_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "policy_generation": null,
        "predecessor_policy_sha256": null
      }
    ]
  },
  "work_items": [
    {
      "work_id": "fix-384",
      "opportunity_id": "agentlily-267",
      "started_at_utc": "2026-09-13T13:00:00.000Z",
      "free_work_budget_minutes": 240,
      "free_work_spent_minutes": 90,
      "payoff_path": "<full payoff-path object>"
    }
  ]
}
```

For a successor continuity generation, retain the complete canonical ledger prefix, append new facts, increment `generation` by one, and set `previous_receipt_sha256` to the canonical digest of the exact prior receipt. A successor owner policy additionally increments `policy_generation` and binds the exact predecessor policy digest.

## CLI

Production compilation deliberately has no caller-supplied `--as-of` override. Current process UTC is authoritative.

```bash
python -m concierge.payoff_path_gate compile \
  --input work.json \
  --packet payoff.packet.json \
  --markdown payoff.review.md \
  --receipt payoff.receipt.json \
  --previous-receipt prior.receipt.json

python -m concierge.payoff_path_gate verify \
  --input work.json \
  --packet payoff.packet.json \
  --markdown payoff.review.md \
  --receipt payoff.receipt.json \
  --previous-receipt prior.receipt.json
```

`--previous-receipt` is omitted for continuity generation 0.

Outputs are create-exclusive. Descriptor-bound custody rejects unsafe path generations. Duplicate JSON keys, unknown fields, bool-as-int values, unsafe integers, noncanonical UTC, malformed SHA-256, secret/PII-shaped durable refs, malformed policy chains, and non-HTTPS source URLs fail closed.

## Verification model

Verification re-normalizes exact inputs, rejects future packet evaluation times, recompiles the original packet exactly, verifies receipt/Markdown content addressing, enforces continuity and owner-policy ancestry, and then re-evaluates temporal validity at verifier current time. A previously READY item that is no longer current fails verification.

## Authority ceiling

The gate performs offline owner decision support only. It cannot authorize or perform buyer/platform/prime contact, email/SMS/DM/calls, supplier registration, competition entry, bid/proposal/work submission, contract/signature action, source work outside its own carrier, delivery/fulfillment, invoice/payment/refund, bank/provider mutation, spend/deployment, tax/accounting/legal determinations, award/payment/cash assertions, or recognized-revenue claims.
