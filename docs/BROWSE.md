# Browse

`concierge browse` reads the existing bounty collector once through `fetch_bounties_report()`. It does not retry, sleep, refetch, or start another collector.

## Flags

| Flag | Meaning |
|---|---|
| `--repo` | One or more `owner/repo` names, or a short name under `Scottcjn/` |
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
