# Reuse issue-state observations before bounty sharding

A public catalog can lag GitHub. Keep the original sanitized catalog, capture the
current issue state once, and share an offline overlay instead of having every
worker query the same issues. This is a source-state filter for **new** candidate
work, not an assignment, payment check, or restriction on repairing an existing
team submission.

## Capture and reconcile

Supply a small JSON envelope containing actual issue responses and their capture
time. The example below is synthetic; replace it with observations, not assumptions.
Use GitHub issue URLs and issue numbers. Both REST responses (`number`, `html_url`,
`url`) and connector responses (`issue_number`, canonical `url`) are accepted.
Do not pass pull-request collection entries.

```json
{
  "retrieved_at": "2026-10-04T23:00:00Z",
  "issues": [
    {
      "number": 1,
      "html_url": "https://github.com/owner/repo/issues/1",
      "state": "closed",
      "assignees": []
    }
  ]
}
```

```sh
python tools/bountyhub_reconcile.py \
  --input work/bounty-intake/20261004-bountyhub-compact.json \
  --github-snapshot github-issues.json \
  --output bountyhub-reconciled.json
python tools/bountyhub_shard.py \
  --input bountyhub-reconciled.json \
  --all-workers --worker-count 8 \
  --output bountyhub-wave-plan.json
python tools/bountyhub_shard.py \
  --input bountyhub-reconciled.json \
  --worker-index 0 --worker-count 8
```

The reconciler accepts standard v1 and compact-v1 sanitized intake snapshots.
It makes **zero network requests** and copies only state and assignee logins from
issue responses. Share the reconciled output, not raw issue/account payloads.
Original rows, duplicate listing IDs, funding amounts and provider reasons are
retained; amounts are never summed. SHA-256 fields bind the CLI output to both
input captures. The output path may not replace either input.

Only a catalog candidate with fresh, explicitly open and unassigned evidence gets
`reconciled_candidate=true`. Missing observations, unknown fields, conflicting
captures, closed issues and assignments remain visible with distinct statuses.
An empty result does not mean that no work exists elsewhere.

`--max-age-seconds` controls observation recency (default 900 seconds).
`--as-of` fixes the evaluation time for an offline reproduction; it does not make
an old capture live. The sharder checks the original capture time again against
its own current clock, so a previously fresh overlay cannot remain fresh forever.
Refresh one shared capture when needed rather than making every worker refetch it.
Catalogs without an overlay retain the existing advisory sharding behavior. Use `--all-workers` once per wave to emit one authoritative manifest containing every explicit slot, a stable candidate-set fingerprint, and occupancy counts. Share that plan and assign each `worker_index` at most once instead of letting every worker independently choose a slot. This removes coordinator-side index collisions without adding provider traffic. Individual workers can still use `--worker-index`; `--worker-key` remains available for backward-compatible hashed assignment.

This observation does not establish an available platform claim, absence of a
competing PR, escrow, acceptance or payout. Reconcile current team ownership and
the sponsor's claim process before starting new work. No attribution or payment
policy is implemented by these tools.

## Focused checks

```sh
python -m unittest discover -s tests -p 'test_bountyhub_reconcile.py'
python -m unittest discover -s tests -p 'test_bountyhub_shard.py'
```
