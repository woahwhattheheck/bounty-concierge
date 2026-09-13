# Receivables aging review

`concierge.receivables_aging` closes the gap between **merged work** and **verified wallet settlement**. It produces a deterministic owner-review queue for RTC bounty work that is still wholly or partially unsettled as it ages.

A merge is never cash. An advertised reward is never cash. The compiler re-runs both upstream authority boundaries each time: current GitHub PR closeout through `revenue_closeout.build_closeout_queue`, then canonical wallet history and `revenue_settlement.reconcile_cash`. The public API exposes no caller-selected `as_of`, captured-wallet shortcut, or realized-cash receipt input that could be resealed into authority.

## Policy

Policy is strict JSON:

```json
{
  "schema": "bounty-receivables-aging-policy/v1",
  "version": 1,
  "followup_after_hours": 24,
  "escalate_after_hours": 72
}
```

`escalate_after_hours` must be greater than `followup_after_hours`. Both thresholds are integer hours. Current UTC is verifier-owned.

## Actions

For every live `MERGED` item the receipt preserves advertised RTC, verified RTC, exact outstanding RTC, merge age, payment-evidence identities, and any existing settlement-followup URL.

- `SETTLED_VERIFIED`: canonical wallet evidence equals the full advertised RTC amount.
- `MONITOR_UNSETTLED`: unpaid/partial and younger than the follow-up threshold with no routed follow-up.
- `OWNER_FOLLOWUP_REVIEW`: unpaid/partial, no routed follow-up, and old enough for owner review.
- `MONITOR_ROUTED_FOLLOWUP`: a settlement follow-up is already routed but the merge has not reached escalation age.
- `OWNER_ESCALATION_REVIEW`: a routed follow-up exists, verified cash is still incomplete, and merge age reached the escalation threshold.

These are review states, not autonomous actions. A routed URL is evidence that a follow-up path exists; it is not evidence that the sponsor promised payment or that any contact should be sent now.

Non-merged manifest items remain bound into `source_scope_sha256` but never become receivables. This keeps the live scan scope explicit without turning open/closed-unmerged work into a cash claim.

## CLI

```bash
python -m concierge.receivables_aging \
  closeout-manifest.json payment-bindings.json receivables-policy.json \
  --wallet "$RTC_WALLET" \
  --output receivables-review.json
```

The manifest uses the existing `revenue_closeout` schema. Bindings use the existing settlement schema. The command queries live GitHub state and canonical wallet history; provider/read failures fail closed.

The output receipt includes a canonical `receipt_sha256`. `verify_receipt_integrity()` checks only self-integrity of a captured receipt. **It does not establish currentness.** To know current receivables, rerun the compiler so GitHub and wallet authority are read again.

JSON parsing rejects duplicate keys. File output is exclusive and refuses to overwrite an existing path, including a final symlink.

## Authority ceiling

The strongest states are `OWNER_FOLLOWUP_REVIEW` and `OWNER_ESCALATION_REVIEW`. They do **not** authorize sponsor/customer contact, collection demands, bounty claims or submissions, wallet/provider mutation, invoices/refunds/credits, purchase/spend, accounting/tax conclusions, cash recognition from merge state, or future-revenue assertions. Any outbound action remains a separate owner decision.
