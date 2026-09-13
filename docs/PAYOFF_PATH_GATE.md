# Payoff Path Gate

`concierge.payoff_path_gate` is an offline owner-review gate for one question:

> **Before compensation is secured, is there a current evidence-backed payoff path, and is cumulative unpaid effort still inside an explicit owner cap?**

It is deliberately **not** an expected-value engine, a buyer-facing sales tool, payment authority, or a revenue-recognition surface.

## v2 continuity model

`payoff-path-work/v2` removes caller-authored cumulative-spend snapshots. Unpaid effort is derived only from immutable effort events, and owner caps are versioned policies linked to the prior receipt.

Each work item carries:

- exact `work_id` + `opportunity_id` identity;
- an owner `budget_policy` with `policy_id`, monotonic `generation`, `cap_minutes`, evidence ref/SHA-256, observed-at time, predecessor-policy digest, and exact superseded-receipt digest;
- append-only `effort_events`, each with a unique per-work event ID, exact work/opportunity identity, integer minutes, observed-at time, and evidence ref/SHA-256;
- the evidence-backed payoff path.

A successor document embeds the exact prior receipt as `predecessor_receipt`. Compilation fails closed when prior work disappears, an effort event is omitted or changed, derived spend goes backward, a same-generation cap changes, a policy generation skips, predecessor links do not match, successor evidence is not strictly later than the predecessor receipt, or a receipt is transplanted across opportunities.

Receipts preserve the complete ordered policy-generation digest history plus every admitted effort-event fingerprint. Exact replay is deterministic and event order is normalized.

A prior `STOP_UNPAID_WORK` cannot return to READY by resetting a spend field because no spend field exists. It can reopen only through a new explicit owner policy generation whose cap exceeds cumulative immutable effort; the result then includes `OWNER_CAP_GENERATION_EXPLICITLY_REOPENED_PREVIOUS_STOP`.

**Custody requirement:** continuity is only as strong as retention of the prior receipt chain. A successor must be compiled against the exact retained predecessor receipt. Discarding the chain and pretending an old work item is fresh genesis is a custody violation outside what an offline hash chain can detect by itself.

## Payoff path evidence

Supported mechanisms and required conversion events remain fixed:

| Mechanism | Required conversion event |
|---|---|
| `BOUNTY` | `SUBMIT_WORK` |
| `COMPETITION_PRIZE` | `ENTER_COMPETITION` |
| `PAID_OFFER_OR_PILOT` | `SEND_PAID_OFFER` |
| `PRIME_SUBCONTRACT` | `SECURE_TEAMING` |
| `REFERRAL_COMMISSION` | `COMPLETE_REFERRAL` |
| `SPONSOR_OR_GRANT` | `APPLY_FOR_GRANT` |

`FIXED` and `POOL` values require positive integer minor units plus a three-letter currency. `NEGOTIATED` and `UNSPECIFIED_BY_SOURCE` must not invent a numeric amount. Source evidence remains canonical HTTPS + immutable ref/SHA-256 + observed-at + freshness bound. Conversion evidence remains event + deadline + immutable ref/SHA-256.

## States and authority ceiling

- `READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW`: current payoff evidence and derived cumulative effort below the owner cap. **Owner review only.**
- `HOLD_NO_PAYOFF_PATH`: no compensation path evidenced.
- `HOLD_STALE_OR_INVALID`: temporal/source/continuity-adjacent evidence invalid for current review.
- `STOP_UNPAID_WORK`: cumulative immutable effort is at or above the current owner cap.

No state means “won,” “accepted,” “paid,” or “revenue,” and no state authorizes outreach, submission, competition entry, spend, delivery, contracting, invoice/payment action, account mutation, or any other external action.

## Genesis input example

```json
{
  "schema": "payoff-path-work/v2",
  "predecessor_receipt": null,
  "work_items": [
    {
      "work_id": "fix-384",
      "opportunity_id": "agentlily-267",
      "started_at_utc": "2026-09-13T13:00:00.000Z",
      "budget_policy": {
        "policy_id": "owner-cap-1",
        "generation": 1,
        "cap_minutes": 240,
        "observed_at_utc": "2026-09-13T13:00:00.000Z",
        "evidence_ref": "owner:cap-1",
        "evidence_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "predecessor_policy_sha256": null,
        "supersedes_receipt_sha256": null
      },
      "effort_events": [
        {
          "event_id": "effort-1",
          "work_id": "fix-384",
          "opportunity_id": "agentlily-267",
          "minutes": 90,
          "observed_at_utc": "2026-09-13T14:00:00.000Z",
          "evidence_ref": "effort:1",
          "evidence_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        }
      ],
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

For a successor packet, carry forward every prior effort event, embed the exact prior receipt, and either retain the exact current policy or advance exactly one policy generation with both predecessor digests populated.

## CLI and verification

Production compilation deliberately exposes no caller-supplied `--as-of` override; process UTC is authoritative.

```bash
python -m concierge.payoff_path_gate compile \
  --input work.json --packet payoff.packet.json \
  --markdown payoff.review.md --receipt payoff.receipt.json

python -m concierge.payoff_path_gate verify \
  --input work.json --packet payoff.packet.json \
  --markdown payoff.review.md --receipt payoff.receipt.json
```

The public wrapper keeps descriptor-bound file custody: non-symlink parent traversal, retained directory descriptors, regular-file generation checks, exclusive output creation, and file/directory fsync. Outputs are never silently overwritten. Strict JSON rejects duplicate keys, non-finite values, unknown fields, bool-as-int values, unsafe integers, malformed SHA-256, noncanonical UTC, secret/PII-shaped durable refs, and unsafe URLs.

Verification re-normalizes the exact document, enforces predecessor continuity, recompiles at the packet's recorded evaluation time, requires exact packet/Markdown/receipt equality, and re-checks current temporal validity so an old READY packet cannot remain current after source or deadline expiry.
