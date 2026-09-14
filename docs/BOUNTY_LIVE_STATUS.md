# Bounty live-status preflight

`concierge.bounty_live_status` prevents stale bounty aggregators, forged local receipts, and caller-supplied HTTP transports from promoting dead or synthetic work into the qualified work feed.

## Problem

Aggregator rows are useful discovery inventory, but their `OPEN` state, advertised amount, and solver count can lag the canonical issue. A live GitHub read is useful only when the code performing the qualification owns that read. Two tempting shortcuts are unsafe:

1. a receipt protected only by a public SHA-256 digest can be fabricated and resealed by any caller; and
2. a public `session=` or transport callback lets the caller manufacture a synthetic GitHub response while the receiving code labels it authoritative.

Schema v3 removes both authority paths.

## Public API

### `preflight_further_qualification(...)`

This is the **only positive qualification path**. It performs a fresh GitHub issue GET through code-owned transport and returns an immediate decision object:

```python
from concierge.bounty_live_status import preflight_further_qualification

result = preflight_further_qualification(
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

if result["clear_for_further_qualification"]:
    # Continue immediately into bounty_preflight / bounty_availability.
    # This is not dispatch, payment, or revenue authority.
    pass
```

The positive bit is valid only as the direct result of that acquisition. The decision object is intentionally not sealed, HMAC-verifiable, durable, or replayable. A later action must call the function again immediately before relying on live state.

### `inspect_live_status(...)`

This performs the same code-owned GitHub acquisition but returns only a schema-v3 audit receipt. It accepts no `session`, transport, callback, or caller-selected clock. The receipt records provider classification and provenance but is never current qualification authority.

### `verify_receipt(receipt)`

This verifies the receipt's public SHA-256 **self-integrity only**. It does not prove that GitHub was queried and does not authorize qualification.

### `is_clear_for_further_qualification(receipt)`

This compatibility accessor now always returns `False`. Retained receipt bytes can never produce a positive result, even when the receipt is internally consistent, currently dated, and says `OPEN`. Use `preflight_further_qualification()` instead.

## Classification model

A code-owned acquisition classifies the target as one of:

- `OPEN` — GitHub returned a canonical open issue at capture time.
- `CLOSED` — GitHub returned the canonical issue as closed.
- `DELETED_OR_MOVED` — GitHub returned `410 Gone` for the resolved API target.
- `UNVERIFIABLE` — provider errors, ambiguous `404 Not Found or inaccessible`, malformed payloads, identity conflicts, PR substitution, issue-number-changing redirects, cross-host redirects, or redirect query/fragment ambiguity fail closed.

A repository rename or move is accepted only when GitHub's final API URL and returned canonical `html_url` agree on repository identity and issue number. Inputs and redirects must use credential-free HTTPS without query strings or fragments.

## Discovery metadata

The optional discovery object may contain only:

- `source_name`
- `source_url`
- `observed_at`
- `advertised_amount`
- `advertised_state`
- `solver_count`

The module never fetches `source_url`. Discovery state, amount, and solver count never affect the provider classification or the positive decision.

## Credential semantics

`token=None` uses the configured ambient `GITHUB_TOKEN`. Passing `token=""` explicitly suppresses ambient credentials and sends no `Authorization` header. A `404` remains `UNVERIFIABLE` whether or not a token was supplied because the module does not independently prove that token's access scope over the target.

## Authority ceiling

Neither a receipt nor a positive live-status decision proves that a bounty is funded, available, unclaimed, eligible, payable, or dispatchable. This module performs no claim, comment, submission, sponsor contact, provider mutation, payment mutation, payout inference, cash assertion, or revenue recognition. Existing `bounty_preflight` and `bounty_availability` gates remain mandatory.

## Regression coverage

`tests/test_bounty_live_status.py` covers 32 hostile and functional cases in normal and `python -O` modes, including:

- a fully fabricated, currently dated, correctly resealed `OPEN` receipt that must not advance;
- rejection of the former public `session=` transport-injection primitive;
- a fresh request for every positive decision and a later close observed on refetch;
- stale aggregator `OPEN` versus live `CLOSED`;
- ambiguous 404 with and without a supplied token, plus 410 handling;
- explicit empty-token suppression of ambient credentials;
- canonical repository redirects and issue-number, host, query, and payload identity drift;
- PR substitution, malformed JSON, malformed timestamps, boolean HTTP status, provider failure, and server errors;
- discovery non-authority and no-fetch behavior;
- strict metadata typing and non-GitHub/PR/query input rejection;
- receipt tamper detection and the distinction between self-integrity and authority; and
- explicit no-dispatch/no-payment/no-revenue ceilings.

The focused GitHub workflow runs `py_compile`, normal tests, and optimized tests on Python 3.9 and 3.13.
