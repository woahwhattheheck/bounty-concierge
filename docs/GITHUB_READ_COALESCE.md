# GitHub read coalescing

`concierge.github_read_coalesce` suppresses short bursts of identical,
read-only GitHub checks across coordinated workers.

## Contract

- Cache TTL defaults to **3 seconds**.
- Refresh ownership lasts **5 seconds**.
- States are `FRESH`, `CACHED`, `IN_FLIGHT`, and `STALE`.
- Scope, query identity, and worker label are hashed before persistence.
- The module performs no network I/O; the caller supplies the read function.
- Returned cached data is always `advisory_only=True`. A write path still uses
  its normal live expected-head and ownership checks.

## Example

```python
from concierge.github_read_coalesce import GitHubReadCoalescer, coalesced_read

cache = GitHubReadCoalescer(
    "/run/tjlabs/github-read-state.sqlite",
    scope="managed-app:actor-293",
)

receipt = coalesced_read(
    cache,
    "github:repo/example:pr:123",
    owner="worker-7",
    reader=fetch_pr_123,
)

if receipt.status == "IN_FLIGHT":
    defer_without_calling_github(receipt.retry_after_seconds)
else:
    use_for_discovery(receipt.payload)
```

Keep payloads small (hard limit: 256 KiB) and make query identities canonical.
If a reader raises, its refresh ownership is released immediately. Generation
checks prevent an expired worker from overwriting a newer refresh.
