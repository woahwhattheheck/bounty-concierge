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
| explicit provider points decision | `noncash_recognitions` with its native unit | money award, eligibility, payout readiness or payment |

A GitHub merge is required for every work item, but **merge state never proves payment**.

## Input contract

Root schema: `bounty-concierge/reward-settlement-input/v1`.

Each case binds:

- a merged `owner/repo#PR`, exact 40-hex merge commit, merge timestamp, and repository-source receipt;
- zero or more source-bound lifecycle events;
- every source to an opaque `source_id`, reference, exact SHA-256, observation timestamp, and authority class.

All timestamps are canonical UTC seconds (`YYYY-MM-DDTHH:MM:SSZ`). Money is positive integer minor units with an uppercase three-letter currency. JSON floats, booleans-as-money, duplicate keys, UTF-8 BOMs, future observations, rebound/reminted source identities, conflicting money/eligibility facts, outgoing transfers, and ambiguous transfer state evolution fail closed.

Transfer IDs and payout-ticket IDs cannot be reused across merged work items. A transfer may have multiple observations only when amount/currency are stable and state progresses monotonically (`PENDING` → `CONFIRMING` → one terminal `CONFIRMED` or `FAILED`). Terminal conflicts/regressions fail closed.

The existing `data/reward_settlement_ledger.synthetic.json` fixture is synthetic and asserts no real sponsor, payment, or revenue fact. The separate [GrantFox example](../examples/grantfox-recognition-20261003/README.md) contains actual public provider comments and merged-PR records.

## Noncash provider recognition

Use `PROVIDER_RECOGNITION` for an explicit award of points. The event has the usual `event_id` and `source` fields, plus:

| Field | Meaning |
| --- | --- |
| `provider` | Stable provider name, normalized to lowercase. |
| `claimant` | Stable claimant handle, normalized to lowercase. |
| `award_id` | Stable, case-sensitive identity for this award; the example uses the provider's GitHub comment ID. |
| `unit` | Native point-unit name, such as `FoxPoints`; no currency conversion. |
| `quantity` | Positive integer explicitly awarded for this work. |
| `reported_account_total` | Optional non-negative integer reported account total, retained only as a source-linked observation. |

Recognition requires `source.authority = PROVIDER` and is bound to the case's exact merged work. One award is identified by provider, claimant, canonical GitHub repository/PR and award ID. Repeated observations of that identity count once; conflicting quantities or units are rejected. Separate awards, claimants and providers remain separate.

Each recognition source must describe one award for one work item. Its reference and exact payload SHA-256 cannot be reused for a different award, even with a new event ID, source ID or observation timestamp. Case variants of the same GitHub work item cannot appear as multiple recognition cases. If identical payload bytes supply different reported account totals, compilation rejects the conflicting extraction. An omitted total may be supplied by another observation of those same bytes; distinct payload captures can retain distinct account-total observations.

Per-case `noncash_recognitions` retain the award quantity, source IDs and any account-total observations. `aggregates.noncash_recognized` sums award quantities by **provider, claimant and unit**, and reports the number of distinct awards. It never sums reported account totals. For the real example, two awards of 35 FoxPoints produce 70 recognized FoxPoints; the second comment's 70-point account total adds nothing.

Points do not change the case's existing settlement, monetary award, eligibility, payout ticket, payout rail or payment fields. They never enter a currency aggregate. New recognition fields are omitted when no recognition events exist, so existing money-only input produces byte-identical JSON, Markdown and receipt output.

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
