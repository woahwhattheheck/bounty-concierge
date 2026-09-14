# Canonical source resolution for paid-work discovery

Discovery feeds are leads, not authority. A mirror row can stay open after the
canonical issue is closed, omit an assignee, hide competing PRs, or duplicate a
work item already represented elsewhere. Bounty Concierge therefore separates
**resolution** from **authorization**.

## Safe path

`concierge-discover` (or `python -m concierge.discovered_revenue_intake`) takes a
JSON discovery record, resolves it to exactly one canonical GitHub issue, and
then immediately runs the existing live revenue intake gates against that
canonical issue.

```json
{
  "listing_url": "https://bounties.example/tasks/widget-17",
  "source_urls": ["https://github.com/acme/widgets/issues/17"],
  "title": "Optional discovery title",
  "body": "Optional discovery text"
}
```

```bash
concierge-discover listing.json --json
```

Resolution precedence is deliberate:

1. A clean `https://github.com/<owner>/<repo>/issues/<n>` listing resolves to
   itself. References in that issue text cannot redirect the canonical identity.
2. An external listing with `source_urls` must expose exactly one distinct
   GitHub issue. Multiple canonical candidates HOLD.
3. Without explicit source URLs, full GitHub issue URLs or qualified
   `owner/repo#number` references in the title/body may resolve the row. Multiple
   distinct candidates HOLD.
4. No unique canonical issue means HOLD. The resolver never guesses.

Free-text fallback uses full-token boundaries on **both** sides. A clean issue
URL extracted from prose is then parsed by the same HTTPS/GitHub issue validator
used for explicit source URLs before it can become a candidate. Decorated or
embedded strings such as `evilhttps://github.com/acme/widgets/issues/17`,
`foo/https://github.com/acme/widgets/issues/17`, `evil/acme/widgets#17`, query
suffixes, or path suffixes do not mint a canonical candidate.

The resolver only returns normalized repository/issue identity, safe counts, and
reason codes. It does not echo mirror paths, listing title/body text, or source
URL text into receipts.

## Resolution is not authorization

A `RESOLVED` result is never enough to dispatch work. The discovered-intake
bridge calls `qualify_live_revenue_intake`, which refreshes canonical GitHub
state and retains the existing gates for issue state, reward authority, formal
assignment, claim pressure, linked-PR competition, maintainer expiry signals,
source provenance, and generation stability.

An ambiguous row therefore returns a receipt such as:

```text
disposition=HOLD dispatch=false basis=EXPLICIT_SOURCE_URL source=none reasons=RESOLVER:CANONICAL_SOURCE_AMBIGUOUS
```

A resolved mirror can still return HOLD or REJECT after the canonical live
checks. Discovery metadata cannot override those decisions.

## Operational use

Use this path before assigning fleet capacity from broad GitHub searches,
third-party bounty boards, copied Slack leads, or other mirror-style feeds. It
is specifically intended to prevent duplicate labor on stale, already-assigned,
heavily-contested, or multiply-listed work while preserving mirrors as useful
discovery signals.
