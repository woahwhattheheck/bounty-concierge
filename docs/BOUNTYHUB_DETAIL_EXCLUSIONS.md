# Spend detail requests on selected issues

`collect --exclude-issues` applies the existing caller-selected issue exclusions
before BountyHub funding-detail requests, rather than spending those requests
and filtering the resulting shortlist afterward. It makes no automatic decision
about which issues to exclude. Without the option, collection is unchanged.

```sh
python -m concierge.bountyhub_catalog collect \
  --max-pages 10 --max-details 20 --exclude-issues exclude.json \
  --output catalog.json
python -m concierge.bountyhub_catalog targets catalog.json \
  --exclude-issues exclude.json --output shortlist.json
```

`exclude.json` uses the existing map: `owner/repo#number` keys, each containing
exactly `reason`, an HTTPS `source_url`, and a timezone-aware `observed_at`.
The bounded CLI loader rejects duplicate JSON fields. The existing normalizer
validates the map before any session is opened or provider request is sent.
Programmatic callers pass the same map as `fetch_catalog(excluded_issues=...)`.
The ordinary funded/promised reward selection flags remain unchanged.

## Evidence and scope

Every catalog listing remains in `listings`, in the original order. A deliberately
skipped detail keeps `funding_status: NOT_REQUESTED` and null funding amounts; it
is not silently treated as funded, assigned, eligible, or paid. Each skipped
listing ID is retained in `detail_exclusions` beside the caller's original reason,
evidence URL and observation time. Multiple cards for one issue stay distinct.

A scoped collection reports `detail_scope: exclude_supplied_issues`.
`details_complete`, `complete`, and its embedded shortlist's `source_complete`
refer to the requested nonexcluded detail scope, not to funding coverage of all
retained rows. Request/detail limits and 429/Retry-After handling still apply to
the remaining work. Excluded records consume neither allowance.

The existing `excluded_targets` result now also describes explicitly excluded
active listings at the advertised floor whose funding was not read. Being in
that list does not establish a qualifying reward. The batch-compatible `targets`
stdout envelope is unchanged.

Exclusions do not become persistent holds. A later `targets` invocation must
explicitly supply its desired exclusion map; without it, unread retained details
make selection partial. An ordinary `resume` does not inherit exclusions and can
fetch those skipped details without rereading catalog pages or complete details.
It preserves the original catalog observation bound and binds its source report
by digest. Update dated evidence deliberately rather than treating it as live.

## Focused request-count check

`tests/test_bountyhub_collect_exclusions.py` uses an in-memory Requests transport
adapter: four listings, two already-covered cards for one issue, two fresh issues.
Full collection followed by filtering makes five requests; pre-read exclusion
makes three and returns the same two fresh targets. With only two detail reads
available, late filtering reaches zero fresh targets and pre-read exclusion
reaches both. These are fixture request counts, not live-provider latency or
fleet-throughput measurements.

The four focused checks cover request savings and preserved evidence, unfiltered
recovery, cooldown/budget coverage, and CLI validation/output. From a checkout
with the normal project dependencies installed:

```sh
python -m unittest discover -s tests -p test_bountyhub_collect_exclusions.py -v
```
