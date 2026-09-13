# Paid-work closeout queue

`python -m concierge.revenue_closeout` builds a read-only action queue for PRs that
already have an advertised paid-work amount. It is intended for acceptance and
settlement follow-through after implementation is in flight, not for discovering or
claiming new bounties.

The scanner re-reads the canonical GitHub pull request, validates the expected author
and optional exact head, then reads all three maintainer feedback surfaces: submitted
pull-request reviews, top-level issue/PR comments, and inline pull-review comments.
Only human OWNER, MEMBER, or COLLABORATOR feedback is actionable; bots, outsiders, and
the operator's own feedback are excluded.

Review authority is separate from notification freshness. The latest decision-bearing
review per maintainer is evaluated regardless of cursor age, so an unresolved
`CHANGES_REQUESTED` remains repair work until that same maintainer later APPROVES or the
review is DISMISSED. COMMENTED reviews do not clear a change request. Current repair
and new maintainer-response obligations outrank merged/closed lifecycle routing; only
when no maintainer obligation remains does a merged PR route to settlement or a
closed-unmerged PR route to investigation.

## Lossless feedback cursor

New integrations should persist the receipt's `next_cursor` and send it back as the
next manifest item's `feedback_cursor`. A cursor contains:

```json
{
  "through_at": "2026-09-13T08:00:00Z",
  "seen_events": {
    "review:123": {
      "at": "2026-09-13T08:00:03Z",
      "version": "COMMENTED|2026-09-13T08:00:03Z"
    },
    "review_comment:456": {
      "at": "2026-09-13T08:00:04Z",
      "version": "2026-09-13T08:00:04Z"
    }
  }
}
```

`through_at` is a safe high-watermark, not scan-completion time. The scanner captures
scan start before any network read and advances the watermark only to five seconds
before that instant. Events observed beyond the watermark are recorded by stable
GitHub endpoint-kind + numeric ID and version, so rerunning the same cursor does not
recreate a response obligation. An event that races in after an earlier endpoint was
read remains beyond the safe watermark and is therefore eligible on the next scan.
Edits to an inline or issue comment keep the stable ID but change `updated_at`, creating
a new version and a new response obligation.

The cursor retains only observed event versions at or beyond the next watermark and is
bounded to 1,000 entries. Legacy manifests may provide `last_seen_at` instead of
`feedback_cursor`; that timestamp is migrated as a cursor with no seen-event map, so
equality may replay once during migration rather than risk loss. Do not provide both
fields.

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
      "feedback_cursor": {
        "through_at": "2026-09-13T08:00:00Z",
        "seen_events": {}
      },
      "expected_head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  ]
}
```

`advertised_amount` accepts bounded exact decimal text or an integer, not binary floats;
source length, significant digits, and exponent are bounded before any network read or
fixed-point rendering so hostile scientific notation cannot amplify memory use. Cursor
timestamps must be timezone-aware and cannot be materially in the future.
`settlement_followup_url`, when present, must be an absolute HTTP(S) URL. Duplicate
repository/PR identities are rejected.

Run:

```bash
python -m concierge.revenue_closeout closeout.json
python -m concierge.revenue_closeout closeout.json --json
```

The queue order is deterministic: requested repairs, maintainer responses,
closed-unmerged investigation, settlement routing, settlement monitoring, then passive
acceptance waits. Within one action class the manifest's operator-supplied order is
preserved; raw advertised numbers are never compared across currencies. Neither merge
state nor an advertised amount is treated as sponsor acceptance, earned revenue, or
payment: every output reports `cash_status: not_inferred`. The module performs no
comment, email, claim, wallet, payment, or provider mutation.
