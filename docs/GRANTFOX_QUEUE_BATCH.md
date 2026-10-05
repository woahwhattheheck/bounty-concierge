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

## Atomic pre-TAKE reservation metadata

Every newly compiled batch includes a `swarm_reservations` object keyed by the
same canonical `owner/repo#issue` identity used by the queue. Each value carries
the existing `swarm-custody-reservation/v1` schema, a canonical
`grantfox:<owner>/<repo>#<issue>` work key, the deterministic
`swarm-custody/v1/<sha256(work_key)>` branch, and the existing
`tools/swarm_claim_reservation.py` helper path.

Workers should reserve that exact work key **before** posting a Slack TAKE or
starting source mutation. `ACQUIRED` or `OWNED` means proceed; `BUSY` means keep
the winning owner and choose different work. The batch compiler itself remains
advisory and makes no provider requests or reservation mutations.

This is additive coordination metadata. Verification still accepts older v1
receipts that predate reservation annotations, while validating the exact mapping
whenever the new fields are present.
