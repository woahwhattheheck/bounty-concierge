# Revenue closeout feedback generations

Settlement closeout treats mutable maintainer feedback as content generations, not as timestamp events.

## Authoritative surface

`concierge.revenue_closeout` is the only positive closeout evaluator. The historical provider/coherence implementation is retained privately for composition and auditability. Direct `concierge.revenue_closeout_core` evaluation fails closed unless the authoritative public module has installed its guarded scanner seam in-process; direct `python -m concierge.revenue_closeout_core` is therefore non-authoritative and exits with an error.

## Exact body acknowledgements

The legacy `acknowledged_review_bodies` manifest field remains supported for review summaries. New manifests may use `acknowledged_feedback_bodies` for all mutable external body-bearing feedback:

```json
{
  "acknowledged_feedback_bodies": [
    {
      "kind": "inline_comment",
      "feedback_id": 700,
      "body_sha256": "<lowercase sha256 of the exact current body>"
    }
  ]
}
```

`kind` is exactly one of `review`, `inline_comment`, or `comment`. `feedback_id` must be a stable positive provider id. A body-bearing external feedback item without a stable positive id fails closed.

A non-empty external `COMMENTED` review summary, inline review comment, or issue comment remains actionable until its exact `(kind, feedback_id, body_sha256)` generation is acknowledged. An edit under the same id automatically produces a new generation and reopens the response obligation even when GitHub's `submitted_at`/`updated_at` timestamp is unchanged or older than `last_seen_at`.

This acknowledgement is operator-owned handled-state only. It is not provider attestation, sponsor acceptance, settlement contact evidence, earned revenue, or paid cash.

## Routing precedence

Current maintainer `CHANGES_REQUESTED` remains highest priority. Unacknowledged mutable body generations then block settlement routing. Only after those obligations are cleared can merge state and separately receipt-bound settlement-contact evidence influence `route_settlement_followup` or `monitor_settlement`.
