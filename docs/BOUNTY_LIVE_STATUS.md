# Bounty live-status preflight

`concierge.bounty_live_status` prevents stale bounty aggregators and stale local receipts from promoting dead work into the qualified work feed.

## Problem

Aggregator rows are useful discovery inventory, but their `OPEN` state, advertised amount, and solver count can lag the canonical issue. A live GitHub read fixes that only at the instant it is captured: an `OPEN` receipt can itself become stale after a maintainer closes, transfers, or removes the issue. GitHub `404` is also deliberately ambiguous for resources that are absent **or inaccessible**, so it cannot prove deletion.

## Authority model

Call `inspect_live_status()` with the canonical GitHub issue URL. Optional discovery fields are retained only as provenance:

- `source_name`
- `source_url`
- `observed_at`
- `advertised_amount`
- `advertised_state`
- `solver_count`

The module never fetches `source_url`, and none of those fields can alter the live classification. GitHub is the only live-state provider in this snapshot.

The result classifies the target as one of:

- `OPEN` — GitHub returned a canonical open issue at capture time.
- `CLOSED` — GitHub returned the canonical issue as closed.
- `DELETED_OR_MOVED` — GitHub returned an unambiguous `410 Gone` for the resolved API target.
- `UNVERIFIABLE` — provider errors, ambiguous `404 Not Found or inaccessible`, malformed payloads, identity conflicts, PR substitution, issue-number-changing redirects, or cross-host/ambiguous redirects fail closed.

A repository rename/move is allowed only when GitHub's final API URL and the returned canonical `html_url` agree on the same issue number and repository identity. The snapshot records that the repository redirected and binds the canonical target.

## Currentness contract

`OPEN` is a short-lived observation, never a durable permission bit. Schema v2 therefore exposes **no raw clear/promotion boolean at all**. It records the provider classification plus `verified_at` and a code-owned five-minute `fresh_until`; the clear decision exists only through the freshness-aware accessor.

Consumers must call `is_clear_for_further_qualification(receipt)`. The accessor:

- verifies the receipt's self-integrity digest;
- requires schema v2 and an `OPEN` snapshot;
- requires the exact built-in five-minute freshness interval;
- samples process-owned UTC, rejecting future-dated and expired receipts;
- accepts no caller-selected clock and no caller-selected TTL.

If it returns `False`, call `inspect_live_status()` again before advancing work. A fresh read that observes a later close will return `CLOSED`.

The SHA-256 seal is self-integrity only. It is **not** provider authentication. Persisted receipts from an untrusted source must not be treated as proof that GitHub was actually queried; reacquire live provider state instead.

## Credential semantics

`token=None` means use the configured ambient `GITHUB_TOKEN`. Passing `token=""` explicitly suppresses ambient credentials and performs the request without an `Authorization` header. A `404` remains `UNVERIFIABLE` regardless of whether some token was supplied because the module does not independently prove that token's access scope over the target.

## Example

```python
from concierge.bounty_live_status import (
    inspect_live_status,
    is_clear_for_further_qualification,
)

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

if is_clear_for_further_qualification(receipt):
    # Continue immediately into bounty_preflight / bounty_availability.
    # Do not dispatch solely from this snapshot.
    pass
```

## Safety boundaries

- GitHub issue URLs and GitHub API issue URLs are accepted; PR URLs and non-GitHub authority URLs are rejected before network I/O.
- Discovery URLs are provenance only and are never fetched, avoiding an aggregator-controlled SSRF/redirect surface.
- Redirects must terminate at `api.github.com` and preserve the issue number.
- The canonical payload must agree with the final API repository/issue identity.
- `404` never proves deletion; it is `GITHUB_NOT_FOUND_OR_INACCESSIBLE` / `UNVERIFIABLE`.
- `advertised_amount` is not payout proof.
- `solver_count` is not claim/occupancy authority.
- Current `OPEN` is only a prerequisite for deeper qualification and does not prove a bounty is funded, available, unclaimed, eligible, payable, or dispatchable.
- No external claim/comment/submission, sponsor contact, provider mutation, wallet/payment mutation, payout inference, cash assertion, or revenue recognition occurs.

## Regression coverage

`tests/test_bounty_live_status.py` covers stale aggregator OPEN vs live CLOSED, OPEN-receipt expiry, close-after-capture refetch, future-clock replay, TTL extension attempts, ambiguous 404 with and without a supplied token, 410 terminal hold, explicit empty-token suppression of ambient credentials, repository redirects, issue-number drift, cross-host redirects, canonical identity conflict, PR substitution, malformed JSON, provider failure, discovery non-authority/no-fetch behavior, strict metadata typing, non-GitHub/PR input rejection, API URL normalization, and receipt tamper detection. The focused suite runs in normal and `python -O` modes on Python 3.9 and 3.13.

This module is intentionally upstream of the existing `bounty_preflight` and `bounty_availability` authorities. It does not weaken or replace their maintainer-signal, assignment, competition, credential, provenance, or dispatch gates.
