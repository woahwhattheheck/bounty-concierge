# Cross-program work reward settlement ledger

`concierge.work_reward_ledger` is the evidence-only layer between paid-work lifecycle signals and actual settlement receipts. It exists because these facts are **not interchangeable**:

`advertised → claimed → submitted → accepted → awarded → payment_pending → paid`

`rejected` and `withdrawn` are terminal non-payment states. A later event cannot move a work item backwards, and a paid item cannot be rewritten as rejected/withdrawn.

## What counts as paid

Only a `paid` event with an explicit settlement receipt contributes to `recognized_paid_amount`. A merge, acceptance, award, provider ticket, invoice/payment rail, or pending status contributes **zero** paid value by itself.

Every non-synthetic event must carry provider evidence with:

- a credential-free HTTPS `source_uri`;
- a `source_kind` that is not `synthetic_fixture`;
- canonical UTC `observed_at`;
- immutable lowercase SHA-256 content/evidence identity.

A settlement receipt has its own `receipt_id`, amount, URI, observation time, and SHA-256. Receipt IDs are globally single-use within a ledger. Later lifecycle evidence cannot predate earlier evidence, and the settlement receipt cannot predate the paid event that binds it.

## Denominations and conversion

Amounts are exact decimal strings plus an uppercase denomination such as `USD` or `RTC`. Advertised, awarded, and settled values for one work item must remain in the same denomination. The ledger **never converts currencies or tokens**. Aggregate paid totals remain partitioned by denomination.

This is intentionally compatible with the repository's existing RustChain-specific settlement stack rather than replacing it. `revenue_settlement` and `settlement_registry` remain the authoritative primitives for RTC wallet-history proof/custody. Their verified receipts can be represented here as one cross-program settlement event without widening their authority.

## Deterministic compile, verify, and merge

```bash
python -m concierge.work_reward_ledger compile data/work_reward_ledger.synthetic.json --format json > /tmp/ledger.json
python -m concierge.work_reward_ledger verify /tmp/ledger.json --format markdown
python -m concierge.work_reward_ledger merge stream-a.json stream-b.json --format json
```

The compiler embeds canonical events, deterministic summaries, per-work event/evidence digests, denomination-partitioned paid totals, an immutable authority ceiling, and a top-level `ledger_sha256`. Verification recomputes the digest **and recompiles embedded events** so a caller cannot tamper with summary rows while leaving source events intact.

`merge` coalesces only byte-semantically identical canonical events at the same `(work_id, sequence)`. A distinct event at the same position is a conflict. Event sequences must remain contiguous from 1 per work item, preventing silent history loss.

## Synthetic fixture

`data/work_reward_ledger.synthetic.json` contains explicitly synthetic Frantic-style USD and RustChain-style RTC examples. It uses `example.invalid` receipt URLs and `synthetic_fixture` evidence. These rows demonstrate schema behavior only and are **not claims about real awards or payments**.

## Authority ceiling

This module never performs provider mutation, submission, external contact, invoice creation, payout initiation, transfers, wallet mutation, FX conversion, or accounting/tax recognition. It is a custody and reporting surface for evidence that already exists.
