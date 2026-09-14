# Revenue closeout: mutable review-body generations

GitHub pull-request review summaries are mutable after submission, but the review
object exposes the original `submitted_at` rather than a reliable body-edit
clock. `concierge.revenue_closeout` therefore does **not** use `submitted_at` as
proof that the current body predates an operator cursor.

## Fail-closed rule

For an external maintainer `COMMENTED` review with a non-empty body, the current
body generation remains `respond_to_maintainer` until the manifest acknowledges
that exact provider generation:

```json
{
  "acknowledged_review_bodies": [
    {
      "review_id": 123456,
      "body_sha256": "<lowercase sha256 of the exact current review body UTF-8 bytes>"
    }
  ]
}
```

The acknowledgement is operator-owned state. It means only: “this exact review
ID/body digest was handled.” It is not GitHub attestation and it does not assert
when the body was edited. A later edit changes the digest and automatically
reopens the obligation even though GitHub may keep the old `submitted_at`.

The list is optional, bounded, rejects duplicate review IDs, and requires exact
keys plus a positive integer ID and lowercase SHA-256 digest. Old manifests
remain valid; without an acknowledgement, a non-empty mutable `COMMENTED`
summary is conservatively actionable.

## Output receipt

Results include:

- `acknowledged_review_body_count`: exact current provider generations matched by
  manifest acknowledgements;
- `unacknowledged_review_body_count`: current non-empty `COMMENTED` generations
  still requiring acknowledgement;
- `unacknowledged_review_body_generations`: body-free receipts containing
  `review_id`, `body_sha256`, author, provider `submitted_at`, and URL.

An operator can inspect/handle the review, then copy the exact `review_id` and
`body_sha256` pair into the next manifest generation. The queue never exposes or
stores the review body in its receipt.

## Authority ordering

Current maintainer `CHANGES_REQUESTED` state remains highest priority. Fresh
issue comments and inline review comments remain timestamp-driven. Empty
`COMMENTED` parent notifications retain the existing cursor/child-dedup rules.
Only non-empty mutable review summaries use the explicit generation
acknowledgement contract.

Merged work cannot route into settlement while an unacknowledged review-body
generation exists. Settlement routing is still independently controlled by the
bound send-receipt rules in `REVENUE_CLOSEOUT_CONTACT_EVIDENCE.md`; a route URL
alone never proves contact or payment.
