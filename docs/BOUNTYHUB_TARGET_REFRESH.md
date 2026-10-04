# Refresh known BountyHub listings without repeating discovery

`concierge bountyhub refresh` reads current details for explicit listing IDs from
one retained catalog. It uses the existing transport, identity checks, pledge
normalizer and economic selector. Unlike `resume`, it refreshes already-complete
rows. Unlike `collect`, it makes no catalog-page requests and cannot discover new
listings. No claim, publication, account change or payment is performed.

## One collector, shared evidence

Choose the IDs belonging to the current work item after reading its ownership.
Use the saved catalog from the existing collector; do not ask every builder to
collect again. For example, with `LISTING_ID` set to an actual ID in that catalog:

```bash
concierge bountyhub refresh catalog.json \
  --listing-id "$LISTING_ID" > refresh-01.json
```

Repeat `--listing-id` for another known listing. Between 1 and 100 input IDs are
accepted; duplicate identities are visited once, in first-occurrence order. Unknown IDs and
malformed source identities fail before any request. All callers inherit the
catalog's original reward floor and explicit promised-reward mode; this command
does not silently change a $50 capture into a $25 discovery scope.

The default HTTP request budget equals the distinct listing count. Each outgoing
request, including a redirect hop, consumes that budget. To allow one known
listing to follow a redirect, set an explicit allowance:

```bash
concierge bountyhub refresh catalog.json \
  --listing-id "$LISTING_ID" --max-requests 2 > refresh-with-redirect.json
```

`--max-requests` accepts an integer from 0 through 100 and is recorded as
`request_limit` beside the actual `requests_made` count. It changes only the
transport allowance, without adding listing IDs, catalog reads, or automatic
retries. Zero performs no HTTP requests, opens no session, and retains the selected
records as unattempted in a partial receipt. Increasing the allowance does not
override a retained cooldown; pass the previous receipt when repeating a refresh.

After respecting any provider and shared-workspace cooldown, pass the latest
refresh receipt when deliberately updating those details again:

```bash
concierge bountyhub refresh catalog.json \
  --listing-id "$LISTING_ID" \
  --previous-refresh refresh-01.json > refresh-02.json
```

Use distinct filenames: shell redirection must never truncate an input. The
receipt's `Retry-After` is enforced before a session opens, conservatively from
its observation end, including when the next selection differs. A receipt must
reference the same source catalog digest. The initial catalog's cooldown is also
honored. HTTP 429 and other shared failures stop later requests; 404/410 removal
and invalid detail data remain explicit partial outcomes while independent rows
continue. A 404/410 carrying a positive `Retry-After` still stops later reads;
resume only after its cooldown. Missing or zero retry guidance retains normal
per-listing continuation. There are no automatic retries, sleeps, watchers or
background tasks.

This is **not a cross-process lock or global rate limiter**. Keep one designated
collector for overlapping work, share its latest receipt and honor any broader
provider cooldown. Missing retry guidance does not permit ignoring a rate limit.
The function owns a session it creates; a supplied session remains caller-owned.

## Read the scope correctly

Output uses `bountyhub-target-refresh/v1`, not the catalog schema. `records`
contains only requested identities, individual request intervals and freshly
normalized details. A failed or unattempted row has `listing: null`; old funding
is never substituted. `candidates` contains only issues qualifying from successful
fresh reads. Per-issue listing associations preserve separate funding pools.

`complete` refers only to the requested details and their economic reduction;
it does not mean the whole catalog is current, the bounty is available to us,
our claim was accepted, or money was earned. Existing claim owners, required
assignment and sponsor submission rules still apply. Open-claim counts are
observations, not an exclusive-assignment decision. A read can be complete while
producing no candidate because the listing closed or fell below the saved floor.
Marked prior payouts and unknown payment-status amounts remain unresolved rather
than being advertised as new available money.

`source_sha256` binds the original canonical JSON (sorted keys, compact separators,
finite values). Original observation timestamps and `catalog_observed_through`
remain unchanged; `catalog_refreshed` is always false. Fresh records have their
own timestamps. Early v1 captures lacking `page_size` are accepted for this
page-free operation with `source_page_size: null`; the past page size is not
inferred, inserted into the original or reported as known. Other retained catalog
validation remains the same. The CLI caps each input at 4 MiB.

Exit 0 means this requested scope completed; exit 2 means partial or invalid input.
Input errors name their cause on stderr, such as `invalid listing identity`,
`requested listing is absent from the retained catalog`, or
`retained Retry-After cooldown has not elapsed`. Correct the named input or honor
the saved cooldown before invoking the command again. Invalid JSON includes its
line and column; diagnostics do not echo file contents, paths or malformed timestamps.
Inspect a partial receipt before routing its successful candidates. The receipt
can be reduced by the existing `targets` exporter without another provider read:

```bash
concierge bountyhub targets refresh-01.json > shortlist.json
concierge capture-batch shortlist.json --output-dir capture-run --json
```

The exporter reselects only successful fresh detail records in the requested
scope, using its normal reward-floor options. It ignores the receipt's cached
`candidates` list and never substitutes old catalog rows for failed or unattempted
reads. Its existing `--submission-target-map FILE` and `--exclude-issues FILE`
options apply to these rows as well. The output remains the exact `candidates`
envelope accepted by `capture-batch`; source times, original catalog times and
requested-only coverage appear on stderr. A partial receipt retains exit 2 even
when it contains a usable subset; inspect that subset before starting capture.
An omitted requested record or conflicting detail identity is invalid input.

Keep the original receipt beside the shortlist. It is still not input to
`catalog resume`, and exporting targets does not refresh a GitHub preflight.

Equivalent entrypoints are `python -m concierge.bountyhub_catalog refresh ...`,
`python -m concierge.bountyhub_refresh ...`, and
`refresh_listings(snapshot, listing_ids, previous_refresh=receipt, max_requests=2, session=session)`
from `concierge.bountyhub_refresh`.

## Measured delivery

Run `python examples/bountyhub_target_refresh_demo.py` from a checkout. It loads
the full package and uses native Requests against a local HTTP/1.1 server; only
the network destination is redirected. No public provider is contacted.

The changing 50-listing fixture required **54 wire GETs** for full recollection
(5 catalog pages and 49 active details), versus **2** for two selected identities
supplied three times. Both paths observed the selected listing's new $80 funding
and two open claims; targeted refresh also observed the other listing's closure.
An ordinary `resume` made zero requests and correctly kept its old $50 observation,
showing why it is not a fresh-state alternative. Cooldown, partial-row progress,
legacy metadata, malformed selection, economic filtering and CLI behavior are
covered by the same focused executable, not a new general test framework.

The final executable ran against the locally built and separately installed
wheel, outside the checkout: Python 3.13.5, Requests 2.32.5, Linux x86_64. Installed
`concierge bountyhub refresh --help` also resolved correctly. The retained
[results](../work/throughput/bountyhub-target-refresh-20261004/results.json) bind
source hashes and packaging details. This is fixture request-count evidence,
**not live-fleet latency, production BountyHub validation or a payment result**.
