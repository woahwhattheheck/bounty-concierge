# Merged-work settlement review

`concierge.receivables_aging` is retained as the compatibility entrypoint name,
but its authority model has changed. The live product is now a **merged-work
settlement review**, not a receivables ledger.

The compiler closes the operational gap between current merged bounty work and
canonical wallet evidence without claiming more than those sources establish.
Every run reacquires current GitHub closeout state through
`revenue_closeout.build_closeout_queue`, then current canonical wallet history
through `revenue_settlement.reconcile_cash`.

## Why the v1 receivables schema was retired

The original `bounty-receivables-aging/v1` output called
`advertised_rtc - verified_rtc` an `outstanding_rtc` receivable and let the
presence of `settlement_followup_url` advance routed-followup/escalation states.

Those terms exceeded the evidence:

- GitHub merge proves current merge state. `revenue_closeout` deliberately does
  **not** infer sponsor acceptance, payout eligibility, earned money, or payment
  obligation from a merge.
- Canonical wallet evidence can prove RTC received. The absence of a matching
  wallet transfer cannot prove the remaining advertised bounty is legally or
  contractually owed.
- `settlement_followup_url` is operator manifest route metadata. Its presence
  identifies a possible destination; it is not a provider send receipt and does
  not prove that prior contact occurred.

The v1 output/policy shape is therefore intentionally unsupported. This is a
fail-closed schema migration, not a compatible rename.

## What the new receipt proves

The receipt schema is:

`bounty-merged-work-settlement-review/v1`

For each current `MERGED` RTC item it binds:

- live repository, PR, head and merge time;
- configured advertised RTC amount;
- live wallet-verified RTC amount and payment evidence identities;
- `unverified_advertised_rtc`, defined only as arithmetic
  `advertised_rtc - verified_rtc`;
- merge age using verifier-owned current UTC; and
- optional settlement route metadata from the manifest.

`unverified_advertised_rtc` means **advertised value not verified in the
queried wallet**. It is not a debt, account receivable, payable obligation,
invoice balance, earned revenue, or proof that the sponsor must pay.

Every row explicitly carries:

```json
{
  "route_proves_prior_contact": false,
  "payable_obligation_proven": false,
  "unverified_advertised_amount_is_debt": false
}
```

The summary repeats the authority ceiling with
`unverified_advertised_amount_is_receivable=false`,
`payable_obligation_claim=false`, `debt_claim=false`, and
`prior_contact_claim=false`.

A fully wallet-verified row can reach `SETTLED_VERIFIED`. That is a cash-evidence
state, not an accounting or tax conclusion.

## Policy

Policy is strict JSON:

```json
{
  "schema": "bounty-merged-work-settlement-review-policy/v1",
  "version": 1,
  "review_after_hours": 24,
  "aged_review_after_hours": 72
}
```

`aged_review_after_hours` must be greater than `review_after_hours`. Both are
bounded positive integer hours. Current UTC is verifier-owned; the public
compiler exposes no caller-selected `as_of`.

The old `bounty-receivables-aging-policy/v1` shape is intentionally rejected so
a consumer cannot silently preserve debt/contact semantics after this repair.

## Review states

For an unsettled merged item, age alone selects the owner-review tier:

- `MONITOR_UNSETTLED`: younger than `review_after_hours`;
- `OWNER_UNSETTLED_REVIEW`: at or beyond `review_after_hours`;
- `OWNER_AGED_UNSETTLED_REVIEW`: at or beyond
  `aged_review_after_hours`;
- `SETTLED_VERIFIED`: canonical wallet evidence verifies the full advertised RTC
  amount.

A configured settlement route **does not change these states**. In particular,
there is no `MONITOR_ROUTED_FOLLOWUP` or `OWNER_ESCALATION_REVIEW` transition
based merely on route presence.

These states authorize no outbound action. If a future workflow wants to reason
about “follow-up already sent”, it must consume an independently retained,
provider-grounded send receipt or equivalent authority. A URL alone is not that
receipt.

Non-merged manifest items remain bound into `source_scope_sha256` but do not
enter `merged_work_review`.

## CLI

```bash
python -m concierge.receivables_aging \
  closeout-manifest.json payment-bindings.json settlement-review-policy.json \
  --wallet "$RTC_WALLET" \
  --output merged-work-settlement-review.json
```

The manifest uses the existing `revenue_closeout` schema. Bindings use the
existing settlement schema. The command queries live GitHub state and canonical
wallet history; provider/read failures fail closed.

The output contains a canonical `receipt_sha256`.
`verify_receipt_integrity()` checks captured receipt self-integrity and enforces
the all-false debt/contact authority shape. It **does not establish current
GitHub or wallet authority**. Re-run the compiler for current state.

JSON parsing rejects duplicate keys, non-finite constants and oversized integer
tokens. File output is exclusive and refuses to overwrite an existing path,
including a final symlink.

## Authority ceiling

This module never:

- claims an advertised amount is owed;
- treats unverified advertised value as debt or a receivable;
- infers prior sponsor contact from a configured URL;
- sends sponsor/customer contact or collection demands;
- submits a bounty claim;
- mutates a wallet/provider;
- recognizes accounting/tax revenue;
- authorizes spend; or
- infers future revenue.

If sponsor acceptance, payout eligibility, or prior-contact state becomes
important, that authority must arrive through a separately verifiable source
rather than being inferred from merge state, wallet absence, or route metadata.
