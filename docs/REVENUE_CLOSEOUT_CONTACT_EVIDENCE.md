# Revenue closeout: settlement contact evidence

`concierge.revenue_closeout` is the live paid-work closeout queue. Its settlement route field is intentionally split from settlement-contact evidence.

## Why this exists

A configured `settlement_followup_url` tells an operator **where** a payment or settlement follow-up can be routed. It does not prove that anybody actually contacted the sponsor, maintainer, payer, or provider. Treating route presence as prior-contact truth can suppress the next collection action indefinitely.

The public closeout module therefore adds a separate settlement-contact authority fence on top of the coherent GitHub lifecycle/feedback scanner:

- no route: merged work remains `route_settlement_followup` with `merged_without_settlement_route`;
- route present, no send receipt: merged work remains `route_settlement_followup` with `merged_route_metadata_without_send_evidence`;
- route present plus a valid post-merge send receipt: merged work may become `monitor_settlement` with `merged_followup_send_evidenced`;
- active maintainer change requests, unacknowledged mutable review-summary generations, and new maintainer response work still outrank settlement routing.

This is a read-only owner queue. It does not send a follow-up, prove sponsor acceptance, create a debt or receivable, mutate a wallet/provider, or recognize revenue.

## Receipt contract

`settlement_followup_evidence` is optional. When present it must contain exactly:

```json
{
  "repo": "owner/repository",
  "pr": 123,
  "provider": "gmail",
  "receipt_ref": "opaque-provider-message-id",
  "receipt_sha256": "<lowercase sha256 of the captured provider receipt/evidence>",
  "sent_at": "2026-09-13T15:30:00Z",
  "settlement_route_sha256": "<lowercase sha256 of the exact settlement_followup_url UTF-8 bytes>"
}
```

The compiler enforces:

1. receipt repo/PR identity equals the closeout item;
2. a settlement route exists;
3. the route hash binds the receipt to the exact configured route;
4. provider, receipt reference, and hashes are bounded canonical values;
5. the send time is timezone-aware, is not before the GitHub merge, and is not in the future;
6. send evidence attached to currently unmerged work fails closed.

`receipt_sha256` is evidence custody, not independent provider attestation. The operator is responsible for capturing the provider receipt represented by that digest. A message ID or route URL alone is not enough.

## Output authority

Every result includes:

- `settlement_followup_send_evidenced`: whether a valid bound send receipt was supplied;
- `settlement_followup_evidence`: the normalized evidence object or `null`;
- `settlement_route_proves_prior_contact`: always `false`.

The last field is deliberately explicit so downstream products cannot regress to the old inference that configuring a URL proves contact.

Mutable non-empty `COMMENTED` review summaries use a separate exact-generation acknowledgement fence. See `REVENUE_CLOSEOUT_REVIEW_BODY_ACK.md` for the manifest and output receipt contract.

## Compatibility

Existing manifests remain structurally accepted. The intended settlement behavior remains that an existing `settlement_followup_url` does not suppress the follow-up action by itself. To move a merged item to `monitor_settlement`, add the evidence object after the actual provider send has occurred and a receipt has been captured.

`concierge.revenue_closeout_core.py` now carries the previously reviewed inline-comment pagination, parent/child review deduplication, and bounded PR+feedback coherence fence from the stale #91 repair line. The public wrapper composes that lifecycle authority with settlement-send evidence and mutable review-body generation acknowledgements. Existing queue/CLI entry points remain unchanged.
