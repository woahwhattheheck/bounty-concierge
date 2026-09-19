# GrantFox batch queue receipts

`concierge.grantfox_queue_batch` applies the single-issue GrantFox queue gate to
a whole intake wave and binds the child receipts into one deterministic parent
receipt. It is intended for high-throughput swarm coordination where dozens of
GrantFox cards may arrive at once.

The batch layer deliberately does **not** rank issues, assign dollar values, or
upgrade campaign metadata into reward truth. It reports exact child
dispositions and canonical identities only.

## Input

```json
{
  "schema": "grantfox-queue-batch/v1",
  "snapshots": [
    { "schema": "grantfox-queue-gate/v1", "...": "one fresh provider snapshot" },
    { "schema": "grantfox-queue-gate/v1", "...": "another fresh provider snapshot" }
  ]
}
```

Each child must satisfy the contract in `docs/GRANTFOX_QUEUE_GATE.md`.
The batch rejects an empty list, more than 500 entries, any invalid child, and
duplicate canonical owner/repository/issue identities even when casing differs.

Run:

```bash
python -m concierge.grantfox_queue_batch batch.json --json
```

## Output semantics

Children are sorted by canonical `(owner, repo, issue_number)` so the same set
of snapshots produces the same receipt regardless of input ordering. The parent
contains:

- exact counts for `APPLY_ELIGIBLE`, `WAIT_ASSIGNMENT`,
  `IMPLEMENTATION_ELIGIBLE`, and `HOLD`;
- canonical issue IDs grouped by those dispositions;
- the complete verified child receipts;
- a reward summary that can count `POSSIBLE_DISCRETIONARY` metadata but keeps
  explicit award and verified payment counts at zero; and
- a SHA-256 parent receipt covering all child bytes and summary fields.

`verify_batch_receipt()` recomputes child integrity, uniqueness, canonical
ordering, counts, buckets, reward summary, authority ceiling, and parent digest.

As with the single-issue gate, all provider application, implementation write,
submission, and payment/wallet authority remains false.
