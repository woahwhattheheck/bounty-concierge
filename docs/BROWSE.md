# Browse

`concierge browse` reads the existing bounty collector once through `fetch_bounties_report()`. It does not retry, sleep, refetch, or start another collector.

## Flags

| Flag | Meaning |
|---|---|
| `--repo` | One or more `owner/repo` names, or a short name under `Scottcjn/` |
| `--index PATH` | Filter a retained index or saved live browse report, with no provider read |
| `--skill`, `--tier`, `--min-rtc`, `--max-rtc` | Filters applied after the read |
| `--limit` | Maximum displayed rows (default 20). Not a source-completeness bound |
| `--max-pages` | Passed to the collector. Integer 1-1000, default 100 |
| `--json` | When the read is complete, print the displayed rows as a JSON list |
| `--report` | Print one JSON object with rows and completion metadata |
| `--dry-run` | Print the intended read. No network call |

## Exit

| Code | When |
|---|---|
| 0 | Every requested source finished the pagination GitHub returned |
| 2 | The read is incomplete. Partial rows may still be shown |
| 1 | The page bound or source list is invalid, or another command error |

Incomplete `--json` writes the reason to stderr and does not print a JSON list. Incomplete `--report` prints the object, including `complete: false`, then exits 2.

`COMPLETE` means each requested repository finished its returned pages. It is not an atomic provider snapshot, bounty eligibility, acceptance, or payment. Rate-limit stop, `retry_after_seconds`, and `rate_limit_reset_at` are the collector metadata; browse does not wait or retry.

One HTTP session spans each collector invocation so sequential pages and
repositories can reuse their connection to GitHub. Every response is closed
after consumption, and the session closes when the traversal exits, including
on an error. The collector keeps the same request order, 15-second timeout,
cache validation and rate-limit handling; it does not add parallel reads or
background work.

## Filter one retained collection offline

Workers can apply the ordinary browse filters to an existing index or saved live
`browse --report` without repeating the collector:

```sh
python -m concierge browse --index data/bounty_index.json --skill security --min-rtc 50
python -m concierge browse --index data/bounty_index.json --repo rustchain-bounties --tier major --report
python -m concierge browse --index saved-browse-report.json --limit 5 --json
```

This path uses the same reward filters, unknown-amount handling and stable ranking
as live browse. `--repo` selects retained rows; it does not fetch missing sources.
`--max-pages` has no effect because no provider traversal starts. `--dry-run`
prints an argument-only local plan and does not open the supplied file.

Text starts with `OFFLINE`. Both `--json` and `--report` print one object containing
`mode: offline`, `source`, `rows`, the current filter/display counts and the selected
filters. They never print a bare list that could be mistaken for a fresh live
read. `source` preserves the original collection interval, reported coverage,
collected/filtered counts, retained row count and rows omitted by the saved
report's display limit. The current `filtered_count` counts matches only among
rows actually present in the file. Captured per-repository and rate-limit details
also remain in `source` when supplied. The input file and its timestamp are not
rewritten; filtering does not recover omitted rows or refresh an expired offer.

The input accepts the same bounded index and saved live report shapes as the
[offline announcement preview](OFFLINE_PREVIEWS.md): at most 16 MiB, unique JSON
keys, consistent row counts and valid source timestamps. Whole-input validation
runs before selection. Browse rows also require repository and issue identity;
missing skills and difficulty remain empty/unknown. JSON numbers use the same
representation as live browse, while exact reward text and source excerpts stay
attached to their rows.

Offline exit 0 means the file was processed and did not explicitly report a
partial collection; older indexes with unspecified coverage also exit 0. Exit 2
still prints the selected output when the saved source says `complete: false`.
Input errors exit 1. Read `source.coverage`; none of these exits certifies a
current live queue, eligible assignment, award or payment. The offline JSON
envelope is a filtered result, not a replacement live `browse --report` capture.
Keep the original index/report for later workers and select new output paths
when redirecting stdout.

## Revalidate cached pages

Repeated browsing can reuse issue-page bodies after GitHub confirms that their ETags still match. Caching is optional and off by default. Choose a private directory outside the repository:

```sh
CONCIERGE_BOUNTY_CACHE="$HOME/.cache/bounty-concierge/pages" concierge browse --report
```

Run the same command again to send conditional reads. The environment option also works with the existing `python -m concierge.bounty_index` and `aggregate()` entrypoints. No second collector, schedule, provider write or additional credential is introduced. The API accepts `fetch_bounties_report(..., cache_dir=path)`; `fetch_bounties()` and `aggregate()` forward this option. Explicit `cache_dir=False` ignores the environment setting. Unset `CONCIERGE_BOUNTY_CACHE` or set it to an empty string to disable caching in the CLI.

A cached page is **not** an offline result. Each reuse requires a successful 304 response to its conditional request. A changed 200 response replaces the cached parser inputs. Authentication errors, transport failures, malformed responses, rate limits and unexpected or mismatched 304 validators retain the existing incomplete-report behavior; they do not return stale cached bounty rows. Confirmed quota exhaustion stops later requests even if that response cannot be parsed.

Pagination is re-evaluated on every pass. A 304 can omit its Link header. A full cached page therefore requires a following-page read even when it was previously the last page, so older reopened issues are not hidden. This can make an exactly-full cached page hit `--max-pages` and return PARTIAL until there is enough page allowance to check the tail. PR-only pages still count toward pagination. Traversals remain non-atomic: changes during pagination can still produce an explicit repeated-issue or partial-source result.

With caching enabled, each repository in `--report` includes a `cache` object with `conditional_requests`, `revalidated_pages`, `stored_pages` and `errors`. The source's `http_status` is still the last real provider status, including 304. A corrupt, missing or unreadable entry causes an ordinary GET rather than a cache-only result. Cache-write failures preserve the successful live page and increment `errors`; they do not fabricate a provider failure. Pages without a usable ETag or above the 8 MiB entry bound are not stored. These counters describe this invocation, not measured API-quota or dollar savings.

The loader sizes its initial read to the recorded file length, so small entries do not allocate an 8 MiB input buffer. If a file grows after the size check, it reads the remaining bytes within the same 8 MiB limit plus one overflow-detection byte. JSON, checksum and schema validation still run on every load; this reduces local read allocation without changing provider revalidation.

Cache entries retain only issue fields used by the parser and pagination metadata. They may contain private issue text: do not commit, upload or share the directory. Opaque keys separate repository, page, representation and authorization fingerprint; credential values and unrelated account/profile fields are not stored. A token change starts a different cache partition. Atomic replacement prevents overlapping processes from reading a partly written entry, and a checksum detects accidental damage, not malicious rewriting by someone who can edit the cache. No cached page is accepted solely because its checksum matches.

Removing this directory only removes the optimization; the next read fetches full pages. Old partitions are not automatically deleted. Existing hosted jobs use the cache only when their operator supplies and retains such a directory; this change does not activate hosted caching or claim a deployment.
