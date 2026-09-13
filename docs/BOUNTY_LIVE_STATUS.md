# Bounty live-status preflight

`concierge.bounty_live_status` prevents stale bounty aggregators from promoting dead work into the qualified work feed.

## Problem

Aggregator rows are useful discovery inventory, but their `OPEN` state, advertised amount, and solver count can lag the canonical issue. We have observed discovery rows that still advertised paid work after the canonical GitHub issue was closed or deleted/moved. That wastes builder capacity and increases duplicate-work risk.

## Authority model

Call `inspect_live_status()` with the canonical GitHub issue URL. Optional discovery fields are retained only as provenance:

- `source_name`
- `source_url`
- `observed_at`
- `advertised_amount`
- `advertised_state`
- `solver_count`

The module never fetches `source_url`, and none of those fields can alter the live classification. GitHub is the only live-state provider in this receipt.

The result classifies the target as one of:

- `OPEN` — GitHub returned a canonical open issue. This advances only to the next qualification gate; it is **not dispatch authority**.
- `CLOSED` — GitHub returned the canonical issue as closed.
- `DELETED_OR_MOVED` — GitHub returned 404/410 for the resolved API target. Treat this as a hard lead hold until a human/canonical-source refresh finds the replacement.
- `UNVERIFIABLE` — provider errors, malformed payloads, identity conflicts, PR substitution, issue-number-changing redirects, or cross-host/ambiguous redirects fail closed.

A repository rename/move is allowed only when GitHub's final API URL and the returned canonical `html_url` agree on the same issue number and repository identity. The receipt records that the repository redirected and binds the canonical target.

## Example

```python
from concierge.bounty_live_status import inspect_live_status

receipt = inspect_live_status(
    "https://github.com/example/project/issues/123",
    {
        "source_name": "example-aggregator",
        "source_url": "https://aggregator.example/bounty/abc",
        "observed_at": "2026-09-13T12:00:00Z",
        "advertised_state": "OPEN",
        "advertised_amount": "$1,000",
        "solver_count": 0,
    },
)

if receipt["live"]["clear_for_further_qualification"]:
    # Continue into bounty_preflight / bounty_availability / qualification.
    # Do not dispatch solely from this receipt.
    pass
```

## Safety boundaries

- GitHub issue URLs and GitHub API issue URLs are accepted; PR URLs and non-GitHub authority URLs are rejected before network I/O.
- Discovery URLs are provenance only and are never fetched, avoiding an aggregator-controlled SSRF/redirect surface.
- Redirects must terminate at `api.github.com` and preserve the issue number.
- The canonical payload must agree with the final API repository/issue identity.
- `advertised_amount` is not payout proof.
- `solver_count` is not claim/occupancy authority.
- `OPEN` is only a prerequisite for deeper qualification and does not prove a bounty is funded, available, unclaimed, eligible, or payable.
- Receipts carry a SHA-256 self-integrity digest. This detects mutation but is not provider authentication or payment evidence.

## Regression coverage

`tests/test_bounty_live_status.py` covers stale aggregator OPEN vs live CLOSED, 404/410 holds, repository redirects, issue-number drift, cross-host redirects, canonical identity conflict, PR substitution, malformed JSON, provider HTTP failure, discovery non-authority/no-fetch behavior, strict metadata typing, non-GitHub/PR input rejection, API URL normalization, and receipt tamper detection.

This module is intentionally upstream of the existing `bounty_preflight` and `bounty_availability` authorities. It does not weaken or replace their maintainer-signal, assignment, competition, credential, provenance, or dispatch gates.
