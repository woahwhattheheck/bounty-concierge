# Original-author claim gap audit

Offline MOVA step for turning completed code submissions into *bounded* payout
follow-through work. It detects absent issue-side claims and missing PR-side
claim or compensation language without posting, assigning, or collecting money.

## Run

Capture **current** GitHub PR metadata and **all** canonical issue comments
with pagination. Record the source head, author numeric ID, issue state and
snapshot time. Do not set comments_complete=true if an API response is
truncated, rate limited, or missing pages.

From the repository root:

    python -m concierge.claim_gap_audit /path/to/snapshot.json --fail-on-gaps

Exit 0: valid evidence and no actionable missing claims (or gaps exist without
--fail-on-gaps). Exit 2: actionable gaps with --fail-on-gaps. Exit 1: bad input.
Only one focused optional check:

    python -m unittest tests.test_claim_gap_audit

## Input contract

Each PR in pull_requests must have repo, number, state, head_sha (40 lowercase
hex), author {login,id}, and full current body. Each issue in issues must have
repo, number, pr_number, state, comments_complete and a list of complete
comments {author:{login,id}, body}. The actor must be the actual original GitHub
contributor. The collector, not this offline audit, verifies actor identity,
permissions, canonical URLs, labels and sponsor evidence.

Example (illustrative; not a live funded issue):

    {
      "schema": "mova-claim-gap-audit/v1",
      "actor": {"login": "example-author", "id": 12345},
      "observed_at": "2026-10-09T08:00:00Z",
      "pull_requests": [{
        "repo": "example/repo", "number": 12, "state": "open",
        "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "author": {"login": "example-author", "id": 12345},
        "body": "Closes #5\n/claim #5\nI request eligible bounty compensation."
      }],
      "issues": [{
        "repo": "example/repo", "number": 5, "pr_number": 12,
        "state": "open", "comments_complete": true,
        "comments": []
      }]
    }

A positive report identifies MISSING_ORIGINAL_ISSUE_CLAIM for this example.
It does **not** automatically publish a claim. A new provider read immediately
before any human-approved posting must confirm the exact issue, current PR
source head, original author and comment timeline. Never post a duplicate
claim or use another contributor's credit.

## Safety and meaning

- Every entry is keyed to the canonical owner/repo and issue number.
- A claim made by a different author does not fulfill the original owner's
  claim. A line beginning with /claim #N or /claim owner/repo#N matches only
  that exact target; quoted text and fenced samples do not count.
- Closed issues, closed PRs, changed authors, stale captures (over 24 hours),
  partial comment timelines, and missing source snapshots hold rather than
  generate automated tasks.
- Source claims and issue comments are separate, as are compensation-request
  language and actual settlement. An advertised "Maybe Rewarded" label, bare
  claim command, or receipt of a comment is **not** a cash-award event.
- The snapshot is operator-supplied evidence, **not a provider-signed receipt**.
  Each reported award_status stays NOT_VERIFIED, regardless of result.
- Read-only CLI: no network calls, GitHub writes, Slack posts, new accounts,
  credential handling, payouts, or background runners.

This auditable step supplements the existing MOVA scout/build/QA/publish/
collect flow and preserves its original-author controls.
