# Bounty portal registration gap audit

An **offline**, read-only extension to `bounty-concierge`. Some original GitHub PRs reach review with an affirmative compensation request while the bounty portal only lists another contributor's registered PR. Without reconciling both original PR identities, the fleet can lose the compensation opportunity **despite completed code**.

## Run it alongside bounty work

From the repository root, capture current canonical GitHub PR author, head, state and issue number via an existing GitHub read coordinator/cache. Separately collect the **complete** official IssueHunt issue `Submitted pull Requests` list or BountyHub public bounty's PR-bearing claims list using existing authorized provider readers. Normalize the matching full PR URLs into `registered_pr_urls`. Copy only the captured metadata into a local JSON file. **Never paste keys, account cookies, issue bodies or raw hidden provider data.**

```bash
python -m tools.portal_registration_gap --input local-observations.json --as-of 2026-10-09T05:45:00Z
```

The input is a JSON object with schema `bounty-portal-registration-audit/v1` and `cases` list; each case has exactly `platform`, `repo`, `issue`, `claimant`, `github`, `portal`, and `settlement`. A representative **synthetic** IssueHunt case:

```json
{
  "schema": "bounty-portal-registration-audit/v1",
  "cases": [
    {
      "platform": "issuehunt",
      "repo": "egoist/bili",
      "issue": 183,
      "claimant": "woahwhattheheck",
      "github": {
        "pr_url": "https://github.com/egoist/bili/pull/642",
        "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "observed_at": "2026-10-09T05:43:00Z",
        "state": "open",
        "author": "woahwhattheheck"
      },
      "portal": {
        "source_url": "https://oss.issuehunt.io/r/egoist/bili/issues/183",
        "observed_at": "2026-10-09T05:43:00Z",
        "complete": true,
        "registered_pr_urls": ["https://oss.issuehunt.io/r/egoist/bili/pull/635"]
      },
      "settlement": null
    }
  ]
}
```

Use `complete:false` if the provider page was truncated, unavailable or cannot prove its output list is exhaustive; an empty or stale list must **never** become a confirmed absence. Both independent observations must be current (default at most 24h old) to produce a missing-registration work order. Platform native output aliases are accepted only in the IssueHunt `/r/<owner>/<repo>/pull/<number>` shape; a competing PR is not our submission.

Result stages are `GITHUB_SUBMITTED_PORTAL_NOT_REGISTERED`, `PORTAL_REGISTERED_UNAWARDED`, `AWARDED`, `PAID`, and `UNKNOWN`. Award/paid states require a **separate, same-identity, fresh operator-certified settlement receipt** of the exact shape:

```json
{
  "status": "AWARDED",
  "identity": {
    "platform": "issuehunt", "repo": "egoist/bili", "issue": 183,
    "claimant": "woahwhattheheck", "pr_url": "https://github.com/egoist/bili/pull/642"
  },
  "receipt_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "verified_at": "2026-10-09T05:44:00Z"
}
```

`PAID` must only be used for an actual verified receipt of money delivered to the original beneficiary, **not** a sponsor's funded pool, a portal fee, PR merge, an award promise, or an internal 'paid' label. This offline tool checks consistency and hashes but **cannot authenticate the upstream settlement receipt**; only the authorized operator can do that. If unsure, use `null` and keep cash unverified.

For each demonstrable registration gap, the output emits one `VERIFY_AND_REGISTER_EXISTING_PR_IN_PORTAL` work order with deterministic `operation_id = portal-registration-` + the first 24 hex characters of SHA-256 of the canonical five-field PR identity. A queue consumer deduplicates by this ID across audits and asks the **existing authenticated portal operator** to recheck live IssueHunt/BountyHub state, eligibility and any prior submission receipts before taking any portal action. The tool does not auto-post to Slack, send an application, acquire credentials, place a claim or contact a sponsor.

**Truth boundaries:** a GitHub issue `Closes #N`, a corresponding public IssueHunt issue page and a PR acceptance comment do not establish the original PR's own portal registration. The presence of `#635` says nothing about `#642`; and `PORTAL_REGISTERED_UNAWARDED` says nothing about payment. Run the output with the existing payment ledger; it is an *audit*, not a new centralized permission gate before bounty engineering. Prior provider reads should be cached to avoid rate-limit pressure.
