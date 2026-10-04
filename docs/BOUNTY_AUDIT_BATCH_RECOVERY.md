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

When a shared GitHub request or evidence error raises `BountyAuditError` during
a batch, the command stops immediately. It makes no retry and no requests for
later rows. The canonical issue 404/410 exception below allows independent rows
to continue, while still returning a failed batch. With `--json`, stdout contains
a **PARTIAL object**, not a success-shaped list, and the exit code is 2:

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
    "rate_limit_reset": null,
    "rate_limit_remaining": 0
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

`rate_limit_remaining` retains the parsed `X-RateLimit-Remaining` integer from
that same failed response. Zero remains zero; an absent or non-integer header
is `null`. This observation does not authorize a retry or change stop/cooldown
behavior. An actual Requests HTTP 429 response over loopback produced one GET
and retained status 429, Retry-After `60`, reset `1791116400`, and remaining `0`
through the real JSON reader and partial-report builder. No live provider call
or additional request occurred in that execution.

Without `--json`, completed audit summaries are printed and stderr describes
the partial result. Use JSON when a reusable remaining shortlist is needed.

## Continue past unavailable issue rows

A 404 or 410 from the exact canonical **issue GET** records an
`ISSUE_UNAVAILABLE` outcome and allows later independent rows to continue. A
404 can also reflect access or visibility, so this is an observation of an
unavailable source, not proof of deletion or a bounty eligibility decision.
The response must have no `Retry-After` and must not report zero remaining
primary quota. Authentication failures, ordinary or rate-limited 403s, 429s,
server/transport/evidence failures, and 404/410 responses from search, comments,
or PR-detail reads still stop immediately.

When any issue row is unavailable, the existing PARTIAL report additionally
contains:

```json
{
  "unavailable_count": 1,
  "unavailable_candidates": [
    {
      "input_row": 2,
      "status": "ISSUE_UNAVAILABLE",
      "candidate": {"repo": "example/project", "number": 2},
      "error": {
        "type": "BountyAuditError",
        "message": "GitHub request failed ...",
        "http_status": 404,
        "retry_after": null,
        "rate_limit_reset": null,
        "rate_limit_remaining": null
      }
    }
  ],
  "traversal_complete": true
}
```

This example shows only the additional fields. `rows` still contains only
completed audits. The unavailable candidate retains its original metadata and
explicit submission target, with any old `canonical_audit` removed. Duplicate
case-insensitive issue/target combinations share the unavailable observation
within this invocation, while each input row retains its own outcome. No
negative result is reused across invocations.

Unavailable candidates are excluded from `remaining_candidates`, so the
documented recovery command does not repeatedly stop on the same unavailable
source. Resolve its source identity or access before deliberately adding its
`candidate` to another shortlist. Do not re-add it automatically. Preserve
`unavailable_candidates` alongside the successful audits; `remaining_count: 0`
does not imply success. The report remains PARTIAL and the CLI exits 2.

If traversal reaches the end, `traversal_complete` is true and `failed_row`
identifies the first unavailable input row. If a later shared failure stops
work, `traversal_complete` is false, `failed_row` identifies that later failure,
and `remaining_candidates` begins with that row followed by all later inputs.
The top-level `error` then describes the shared failure and preserves its
cooldown; earlier unavailable outcomes keep their own errors. In either case,
`audited_count + unavailable_count + remaining_count == input_count`.

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
report, including when unavailable issue rows allowed traversal to finish.
A successful call still returns the original list shape. Standalone
`audit_bounty` behavior is unchanged. This is recovery for caught provider/evidence
errors, not a process-crash checkpoint or a persistent cross-run HTTP cache.

## HTTP-error response ownership

The auditor closes a response attached to a Requests failure after copying its
HTTP status and cooldown metadata. This also covers a caller-supplied Session
whose response hook raises an HTTP error before `Session.get()` returns. The
original `RequestException` remains the cause of `BountyAuditError`; a cleanup
failure does not replace it. The caller continues to own its Session.

This matters when an unavailable issue's error is retained while a batch moves
to an independent row. An unread error response can otherwise occupy the only
slot in a blocking connection pool, preventing that later request from reaching
the server. Releasing the response does not retry the failed request or alter
unavailable-row, shared-failure or cooldown decisions. Cleanup is best-effort;
if a custom `close()` fails, connection release is not guaranteed.

The [local reproducer](../examples/audit_http_release.py) uses native Requests,
an HTTP/1.1 loopback server and a blocking one-connection pool. It executes the
exact source definitions used by the audit/error path; unrelated target,
comment and CLI imports are outside this check. No provider requests are made.

```bash
git show 5a8411fdb4a2c32e64b44c2ecc40f16ff470e9df:concierge/bounty_audit.py \\
  > /tmp/audit-before.py
python examples/audit_http_release.py --baseline /tmp/audit-before.py \\
  --output /tmp/audit-http-release.json
```

The retained baseline made one wire request, then remained blocked during the
bounded observation until the fixture explicitly closed its error response.
The repaired source completed all three requests without that rescue, retaining
one audited issue, one unavailable outcome and the PARTIAL result. Both variants
produced the same final report after the baseline rescue, apart from their
loopback port. This is connection-pool progress evidence, not a latency speedup
percentage or a measurement of the live fleet. Full results and source hashes:
[http-error response release](../work/throughput/audit-http-release-20261004-f541/results.json).

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

### Unavailable-row continuation measurement

The actual complete audit module and CLI were compared through Requests
prepared requests, normal status/JSON handling, and controlled response
transport. The normal package bootstrap and all 24 imported `concierge` source
modules were present and matched their pinned Git blobs. No import stubs,
provider calls, dependency install or hosted workflow was used.

For three inputs `[valid, unavailable, valid]`, both 404 and 410 reproduced the
same baseline stall: four GET attempts retained one audit; invoking the documented
remaining-list recovery made one more GET and retained zero additional audits.
The repaired invocation makes seven GET attempts, retains both available issue
audits, and records the unavailable row explicitly with zero remaining rows.
This restores progress for independent work. It does not claim fewer total
requests than manually removing the unavailable row, a live latency improvement,
or measured fleet savings.

Sixteen bounded check groups passed. Eleven unchanged controls compared complete
outputs and requests: successful traversal, 401, 429, throttled and ordinary 403,
503, timeout, issue 404 with retry guidance or exhausted quota, comment 404 and
PR-detail 404. Additional checks cover later throttling after an unavailable
row, duplicate case/explicit-target metadata, and readable partial text output.
The same four-row duplicate fixture reads the unavailable issue once, retains
both distinct input records, and completes the two available audits in seven
GETs. Raw results include the source blobs and exact request paths.

```bash
git show b855a699d70a33c6c9efdd9bcafc330ebeabd3ac:concierge/bounty_audit.py \
  > /tmp/bounty-audit-before.py
python work/throughput/audit-unavailable-20261004/replay.py \
  --baseline /tmp/bounty-audit-before.py \
  --output /tmp/audit-unavailable-results.json
```

Published measurements: [results.json](../work/throughput/audit-unavailable-20261004/results.json).
