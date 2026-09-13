# Paid-work closeout queue

`python -m concierge.revenue_closeout` builds a read-only action queue for PRs that
already have an advertised paid-work amount. It is intended for acceptance and
settlement follow-through after implementation is in flight, not for discovering or
claiming new bounties.

The scanner re-reads the canonical GitHub pull request, validates the expected author
and optional exact head, then reads maintainer reviews and issue comments. It only
routes new human feedback from OWNER, MEMBER, or COLLABORATOR accounts after the
manifest's `last_seen_at` boundary for notification freshness. Review authority is
separate: the latest decision-bearing review per maintainer is evaluated regardless of
cursor age, so an unresolved `CHANGES_REQUESTED` remains repair work until that same
maintainer later APPROVES or the review is DISMISSED. COMMENTED reviews do not clear a
change request. Maintainer comments and COMMENTED reviews are routed for response only
when new; approvals do not create reply spam. A moved expected head fails closed before
feedback is consumed.

Merged PRs are routed either to a missing settlement follow-up or to monitoring an
already-recorded follow-up URL. Closed-unmerged PRs are routed for investigation.
Neither merge state nor an advertised amount is treated as sponsor acceptance, earned
revenue, or payment: every output explicitly reports `cash_status: not_inferred`.
The module performs no comment, email, claim, wallet, payment, or provider mutation.

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

`advertised_amount` accepts exact decimal text or an integer, not binary floats.
`last_seen_at` is required, must be timezone-aware, and cannot be materially in the future. `settlement_followup_url`, when present, must be
an absolute HTTP(S) URL. Duplicate repository/PR identities are rejected.

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
