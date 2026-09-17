# Merged-work reward settlement ledger

`concierge.reward_settlement_ledger` is a read-only compiler for reconciling merged bounty/reward work without turning workflow milestones into fictional cash.

It deliberately keeps these facts separate:

| Evidence | Ledger truth | Never implies |
| --- | --- | --- |
| official/public reward offer | `ADVERTISED_BOUNTY` reference amount | sponsor award, debt, eligibility, payment |
| explicit sponsor/provider award | `SPONSOR_AWARDED` | eligibility, payout ticket, transfer, cash |
| sponsor/provider eligibility decision | `ELIGIBLE_CONFIRMED` / `INELIGIBLE` | award or payment |
| sponsor/provider payout ticket | `PAYOUT_TICKET_OPENED` | transfer |
| supplied payout destination/rail | `PAYOUT_RAIL_SUPPLIED` | provider transfer |
| provider/wallet/bank incoming transfer row | `TRANSFER_EVIDENCED` while non-terminal | payment |
| terminal incoming provider/wallet/bank evidence | `PAID_CONFIRMED` | accounting revenue recognition |
| explicit sponsor/provider decline/expiry/no-reward evidence | `CLOSED_WITHOUT_REWARD` | inferred closure from silence |

A GitHub merge is required for every work item, but **merge state never proves payment**.

## Input contract

Root schema: `bounty-concierge/reward-settlement-input/v1`.

Each case binds:

- a merged `owner/repo#PR`, exact 40-hex merge commit, merge timestamp, and repository-source receipt;
- zero or more source-bound lifecycle events;
- every source to an opaque `source_id`, reference, exact SHA-256, observation timestamp, and authority class.

All timestamps are canonical UTC seconds (`YYYY-MM-DDTHH:MM:SSZ`). Money is positive integer minor units with an uppercase three-letter currency. JSON floats, booleans-as-money, duplicate keys, UTF-8 BOMs, future observations, rebound/reminted source identities, conflicting money/eligibility facts, outgoing transfers, and ambiguous transfer state evolution fail closed.

Transfer IDs and payout-ticket IDs cannot be reused across merged work items. A transfer may have multiple observations only when amount/currency are stable and state progresses monotonically (`PENDING` → `CONFIRMING` → one terminal `CONFIRMED` or `FAILED`). Terminal conflicts/regressions fail closed.

The in-repo fixture is synthetic and asserts no real sponsor, payment, or revenue fact.

## Compile and verify

```bash
python -m concierge.reward_settlement_ledger compile \
  --input data/reward_settlement_ledger.synthetic.json \
  --out-dir /tmp/reward-settlement

python -m concierge.reward_settlement_ledger verify \
  --input data/reward_settlement_ledger.synthetic.json \
  --ledger /tmp/reward-settlement/ledger.json \
  --markdown /tmp/reward-settlement/ledger.md \
  --receipt /tmp/reward-settlement/receipt.json
```

`compile` creates the output directory exclusively and writes deterministic JSON, Markdown, and receipt bytes. `verify` recomputes all three artifacts from the exact source bytes and refuses any difference.

## Aggregates

The ledger publishes three intentionally independent amount buckets by currency:

- `advertised_reference_by_currency`
- `sponsor_awarded_by_currency`
- `paid_confirmed_by_currency`

`recognized_revenue_by_currency` is always empty. Payment evidence is not accounting recognition.

## Authority ceiling

This feature has no authority to contact a sponsor, request payout, submit a payout destination, mutate a provider/wallet/bank, invoice, collect, or recognize accounting revenue. It only reconciles caller-supplied evidence.

## Operator runbook

1. Capture the merge receipt and each relevant sponsor/provider/wallet/bank artifact separately; retain exact bytes and SHA-256.
2. Add only facts the source authority can support. Do not convert prose, silence, merge status, or a payout form into stronger lifecycle states.
3. Compile into a fresh output directory.
4. Read the Markdown case states and the three independent amount buckets. Investigate any missing or conflicting evidence rather than filling gaps by inference.
5. Verify the exact output bytes before using the artifact in a follow-up or settlement review.
6. Any external follow-up remains separately authorized and coordinated; this ledger never sends it.
