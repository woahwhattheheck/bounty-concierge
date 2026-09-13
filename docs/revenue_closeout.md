# Paid-work closeout queue

`python -m concierge.revenue_closeout` builds a read-only action queue for PRs that
already have an advertised paid-work amount. It is intended for acceptance and
settlement follow-through after implementation is in flight, not for discovering or
claiming new bounties.

The scanner re-reads the canonical GitHub pull request, validates the expected author
and optional exact head, then reads maintainer reviews, inline review comments/replies,
and issue comments. It only routes new human feedback from OWNER, MEMBER, or
COLLABORATOR accounts after the manifest's `last_seen_at` boundary for notification
freshness. Because GitHub event timestamps are not unique, events exactly equal to the
cursor timestamp are replayed conservatively rather than risking a same-timestamp miss.
Stable feedback IDs are de-duplicated across pagination; conflicting duplicate snapshots,
malformed inline identities, or exhausted pagination fail closed. For closed PRs and all
exact-head scans, lifecycle authority is additionally fenced across repeated PR snapshots
and two complete normalized feedback collections. If the PR generation or feedback
inventory changes, the scanner retries within a small bound and then fails closed rather
than authorizing settlement from a mixed provider generation. An expected head that moves
through that fence returns `HEAD_MOVED`.

A COMMENTED parent review is suppressed as an overlapping inline notification only when
it has no distinct review body and a concrete child from the same review is at least as
new as the parent submission. Older pending-review children therefore cannot erase a
fresh parent submission at the cursor boundary, and top-level review-body obligations
remain visible. Each distinct inline comment/reply remains actionable. Review authority
is separate: the latest decision-bearing review per maintainer is evaluated regardless
of cursor age, so an unresolved `CHANGES_REQUESTED` remains repair work until that same
maintainer later APPROVES or the review is DISMISSED. COMMENTED reviews do not clear a
change request.

New maintainer issue comments, inline review comments/replies, and non-overlapping
COMMENTED reviews route a response. Current repair and response obligations outrank
merged/closed lifecycle routing; only when none remain does a merged PR route to
settlement or a closed-unmerged PR route to investigation.

Merged PRs with no maintainer obligation are routed either to a missing settlement
follow-up or to monitoring an already-recorded follow-up URL. Closed-unmerged PRs with
no maintainer obligation are routed for investigation. Neither merge state nor an
advertised amount is treated as sponsor acceptance, earned revenue, or payment: every
output explicitly reports `cash_status: not_inferred`. The module performs no comment,
email, claim, wallet, payment, or provider mutation.

## Manifest

```json
{
  "schema_version": 1,
  "items": [
    {
      "repo": "acme/widgets",
      "pr": 17,
      "operator_login": "builder",
      "advertised_amount": "500",
      "currency": "USD",
      "last_seen_at": "2026-09-13T08:00:00Z",
      "expected_head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  ]
}
```

`advertised_amount` accepts bounded exact decimal text or an integer, not binary floats;
source length, significant digits, and exponent are bounded before any network read or
fixed-point rendering so hostile scientific notation cannot amplify memory use.
`last_seen_at` is required, must be timezone-aware, and cannot be materially in the
future. `settlement_followup_url`, when present, must be an absolute HTTP(S) URL.
Duplicate repository/PR identities are rejected.

Run:

```bash
python -m concierge.revenue_closeout closeout.json
python -m concierge.revenue_closeout closeout.json --json
```

The queue order is deterministic: requested repairs, maintainer responses,
closed-unmerged investigation, settlement routing, settlement monitoring, then passive
acceptance waits. Within one action class the manifest's operator-supplied order is
preserved; raw advertised numbers are never compared across currencies. This is
prioritization evidence only, not a cash ledger.
