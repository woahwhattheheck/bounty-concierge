# Live bounty source status

A failed GitHub read is not an empty bounty queue. The normal `concierge browse`
command now emits results only after every requested repository has completed
its pagination. A 404, permission error, transport failure, invalid response,
repeated issue identity, or page-budget exhaustion produces an error and a
nonzero exit. Successful `browse --json` output remains the existing JSON array.
No claim, application, wallet, payment, or sponsor action is performed by browsing.

## Partial results are explicit

Operators building a source-status view can use the report API without discarding
successful reads from other sources:

```python
from concierge.bounty_index import fetch_bounties_report

report = fetch_bounties_report(["Scottcjn/rustchain-bounties", "Scottcjn/bottube"])
for source in report["repositories"]:
    print(source["repo"], source["status"], source["bounty_count"])
# Inspect report["bounties"] as partial observations when complete is false.
```

`complete` describes traversal of the requested source set. It does not establish
an atomic point-in-time GitHub snapshot, assignment availability, claim eligibility,
or an earned reward. `started_at` and `updated_at` delimit the read interval.
`total_count` counts only returned observations; never label it a full queue total
when `complete` is false. Individual source statuses preserve failed and unattempted
repositories instead of silently omitting them.

`fetch_bounties()` retains its list result on success but now raises
`BountyFetchIncompleteError` on incomplete reads. Callers that previously relied
on silent best-effort results should use `fetch_bounties_report()` explicitly.
The exception's `report` attribute retains the same partial observations.
`aggregate()` retains its index keys and adds source/completion metadata; it also
raises instead of emitting an incomplete index. The standalone module exits 2
without a success JSON body; the existing browse command's error handler exits 1.

## Request limits and recovery

Requests are serial, with no automatic retries or sleeps. A 429, a rate-limited
403, or an exhausted successful-response quota defers remaining requests. The
report retains numeric `Retry-After` seconds and `X-RateLimit-Reset` epoch seconds
when supplied. Later repositories receive `NOT_ATTEMPTED_RATE_LIMIT`; they are
unknown, not empty. A source-specific non-throttling error allows other sources
to continue. Authorization headers and raw provider error text are not copied
into diagnostic errors.

Respect GitHub's retry guidance before starting another read. Do not immediately
rerun the entire source set from every swarm worker. The API defaults to at most
100 pages per repository; report callers can explicitly select 1–1,000 pages with
`max_pages`. Reaching the budget with a next-page link means `PAGE_LIMIT`, never
complete. Repeated configured repository names are fetched once, case-insensitively.

GitHub references: [rate-limit response guidance](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
and [pagination](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api).

## Published index remains separate

The nightly workflow continues to use `concierge.bounty_index_publish` for complete,
identity-checked, atomic replacement of `data/bounty_index.json`, followed by the
existing fresh-index README update. This change does not replace that publisher,
add a second publication mechanism, dispatch a workflow, or change reward parsing.
