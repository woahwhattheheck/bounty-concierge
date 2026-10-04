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

If the canonical instructions specify a separate submission repository, pass
the existing explicit submission-target record alongside the discovery listing:

```bash
concierge-discover listing.json --submission-target submission-target.json --json
```

The target file must contain a JSON object with these four fields:

| Field | Value |
| --- | --- |
| `repository` | The submission repository in `owner/name` form. |
| `source_url` | The canonical source URL supporting that submission instruction. |
| `source_content_sha256` | The SHA-256 digest of the supporting source content. |
| `instruction_excerpt` | The source excerpt identifying the submission repository. |

The API accepts the same record as the optional `submission_target` keyword of
`qualify_discovered_revenue_intake`. The bridge forwards an explicitly supplied
record to live intake, whose existing preflight validates its source binding
before network reads. It never discovers or infers a submission target from the
listing. Omitting the option retains the existing behavior; JSON `null`, arrays,
and other non-object target files are rejected. The concise summary does not echo
the instruction excerpt.

Resolution precedence is deliberate:

1. A clean `https://github.com/<owner>/<repo>/issues/<n>` or
   `https://api.github.com/repos/<owner>/<repo>/issues/<n>` listing resolves to
   the canonical web issue URL. References in that issue text cannot redirect
   the canonical identity.
2. An external listing with `source_urls` must expose exactly one distinct
   GitHub issue. Multiple canonical candidates HOLD.
3. Without explicit source URLs, full GitHub issue URLs or qualified
   `owner/repo#number` references in the title/body may resolve the row. Multiple
   distinct candidates HOLD.
4. No unique canonical issue means HOLD. The resolver never guesses.

Native GitHub issue responses expose the web URL as `html_url` and the REST URL
as `url`; either can be used in `listing_url`, `source_urls`, or title/body
references. Web and REST forms of the same issue count as one candidate. The
existing `#issuecomment-<id>` fragment behavior applies to both forms; queries
and other subresources do not identify an issue. Different repositories or
issue numbers remain distinct candidates.

Explicit URLs in `listing_url` and `source_urls` must contain no ASCII control
characters, DEL, or literal spaces. The resolver checks the original string
before URL parsing, which can otherwise remove leading controls/spaces and
embedded tabs or newlines. A malformed URL therefore cannot become a different,
apparently clean GitHub issue reference. Use percent-encoding for spaces in
external listing paths; surrounding prose may still contain ordinary whitespace.

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
