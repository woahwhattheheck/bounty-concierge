# GrantFox carrier-census batch

`concierge.grantfox_carrier_batch` applies the single-issue
`grantfox_carrier_census` contract to an entire discovery wave before workers
publish issues as fresh GrantFox supply.

This closes a throughput gap between two existing controls:

- `grantfox_carrier_census` correctly classifies one issue's implementation
  carriers; and
- `grantfox_queue_batch` batches provider queue snapshots after an issue is
  already considered worth queue evaluation.

The carrier batch is the pre-queue bulk suppression step.

## Input

```json
{
  "schema": "grantfox-carrier-batch/v1",
  "snapshots": [
    {
      "schema": "grantfox-carrier-census/v1",
      "canonical_issue_url": "https://github.com/Acme/repo/issues/10",
      "carriers": [],
      "observed_at": "2026-09-19T22:40:00Z",
      "evaluated_at": "2026-09-19T22:41:00Z",
      "max_snapshot_age_seconds": 900
    }
  ]
}
```

Every child is compiled by the native carrier-census compiler. The batch rejects
invalid children, duplicate canonical issue identities, an empty wave, more than
500 children, and unknown parent fields.

## Output

Children are sorted by canonical `(owner, repo, issue_number)`, making the
receipt permutation-stable. The parent reports exact counts and issue buckets
for:

- `CLEAR_FOR_QUEUE_EVALUATION`
- `REUSE_EXISTING_CARRIER`
- `REAPPLY_WITH_REUSABLE_CARRIER`
- `REVIEW_CLOSED_CARRIER`
- `HOLD`

It also summarizes total relevant carriers, active/merged carriers, reusable
process-closed carriers, and the number of issues suppressed or held for review.

Only `CLEAR_FOR_QUEUE_EVALUATION` proceeds to the existing provider/source
queue gates. Every other state is intentionally non-green.

## CLI

```bash
python -m concierge.grantfox_carrier_batch wave.json --json
```

Exit code 0 means every child is clear for queue evaluation. Exit code 2 means
at least one issue is suppressed or requires review/refresh. This lets a mining
pipeline fail closed before posting a stale fresh-supply batch to Slack.

## Integrity and authority

`verify_carrier_batch_receipt()` verifies every child census receipt, canonical
ordering, uniqueness, disposition counts/buckets, summary totals, the authority
ceiling, and the parent SHA-256 digest.

The batch is evidence aggregation only. It cannot apply to GrantFox, assign an
issue, mutate upstream source, submit work, adjudicate a reward, or move funds.
