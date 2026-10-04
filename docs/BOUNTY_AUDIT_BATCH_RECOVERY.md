# Recover an interrupted canonical audit batch

`python -m concierge.bounty_audit --batch shortlist.json --json` audits a
retained issue shortlist without recollecting the marketplace catalog. The
input is a JSON list, or an exact `{"candidates": [...]}` object. Each row needs
`repo` and a positive integer `number`; existing explicit `submission_target`
records and other row metadata are retained.

## Output and failure contract

A successful traversal keeps the existing JSON list and exit status. An audit
with `canonical_audit.search_truncated: true` still means incomplete search or
comment coverage, and the CLI exits 2 even when every input row returned.

When a GitHub request or evidence error raises `BountyAuditError` during a batch,
the command stops immediately. It makes no retry and no requests for later
rows. With `--json`, stdout contains a **PARTIAL object**, not a success-shaped
list, and the exit code is 2:

```json
{
  "status": "PARTIAL",
  "input_count": 3,
  "audited_count": 1,
  "remaining_count": 2,
  "failed_row": 2,
  "rows": [],
  "remaining_candidates": [],
  "error": {
    "type": "BountyAuditError",
    "message": "GitHub request failed ...",
    "http_status": 429,
    "retry_after": "60",
    "rate_limit_reset": null
  }
}
```

The arrays above are abbreviated. In actual output, `rows` contains the first
completed input row with its `canonical_audit`; `remaining_candidates` contains
the failed input row followed by all later rows in their original order.
`failed_row` is **1-based** and counts input rows, including duplicate entries.
Each count is an input-row count, not the number of unique issues or HTTP calls.
A failure on the first row retains zero audits, not an empty successful census.

An old input `canonical_audit` is removed from remaining candidates so it cannot
be mistaken for a result from this run. All other metadata and explicit targets
stay with their rows. Completed rows retain their existing truncation flags;
`audited_count` does not certify completeness, eligibility, assignment or payment.

`Retry-After` and `X-RateLimit-Reset` are copied only when present on the failed
HTTP response. They are raw provider values, not a calculated retry schedule.
An ordinary 403 is not automatically classified as a rate limit. Timeouts and
invalid JSON may have no HTTP or cooldown metadata.

Without `--json`, completed audit summaries are printed and stderr describes
the partial result. Use JSON when a reusable remaining shortlist is needed.

## Keep the result and resume only unfinished rows

Capture stdout even when the command exits 2. Do not immediately replay the
whole shortlist or build an automatic retry loop:

```bash
status=0
python -m concierge.bounty_audit --batch shortlist.json --json > audit.json || status=$?
printf 'audit exit: %s\n' "$status"
```

Read the error and any provider cooldown. Resolve permission failures through
the existing authorized connection, not a different identity. After a provider
cooldown has elapsed or the actual failure is resolved, extract the remaining
rows locally:

```bash
python - <<'PY'
import json
from pathlib import Path
report = json.loads(Path("audit.json").read_text(encoding="utf-8"))
if not isinstance(report, dict) or report.get("status") != "PARTIAL":
    raise SystemExit("No interrupted-batch report; inspect audit.json and stderr.")
Path("remaining.json").write_text(
    json.dumps(report["remaining_candidates"], indent=2) + "\n", encoding="utf-8"
)
PY

# Run only after the source failure or provider cooldown has been resolved.
python -m concierge.bounty_audit --batch remaining.json --json > remaining-audit.json
```

Keep both output files. The PARTIAL report itself is not valid `--batch` input;
extract `remaining_candidates` as above. Resuming unfinished rows does not refresh
saved audits. Recheck any saved truncated audit separately, and use normal
current-state checks before claiming or submitting work. Do not present a saved
observation as fresh because a later invocation succeeded.

Library callers still receive the original `BountyAuditError` exception type.
For an interrupted `audit_bounties` call, `exc.partial_report` holds the same
report. A successful call still returns the original list shape. Standalone
`audit_bounty` behavior is unchanged. This is recovery for caught provider/evidence
errors, not a process-crash checkpoint or a persistent cross-run HTTP cache.

## Focused execution evidence

The existing full CLI and batch implementation was executed against controlled
HTTP responses using real `requests.Response` status and JSON handling. Python
3.13.5, 18 focused check groups, no live provider requests or dependency installs.
The import fixture supplied inert configuration, the exact existing comment
pagination function, and an unused explicit-target validation guard; it was not
a full-package integration run or an explicit-target validation test.

On three open issues with empty PR searches/comments, issue 1 took three GETs;
issue 2 then failed on its first GET. The original source
`6961270db8626ffccc1ceefa19b8f6f9ce8a2605` produced no stdout after those four
attempts. The repaired path made the same four attempts, retained issue 1 and
returned the two remaining rows with exit 2. A second real CLI invocation on
that extracted shortlist used six GETs instead of nine for a full replay,
avoiding three repeated reads in this fixture. No live-fleet speedup is claimed.

Focused checks also covered first-row failure, ordinary 403, supplied reset
and HTTP-date retry headers, timeout, invalid JSON, malformed issue payload,
mid-issue PR failure, retained truncation, text output, library exception/deep
copy behavior, case-insensitive duplicate rows, zero-read invalid late input,
and unchanged successful, empty-batch and standalone output.
