# Bounty Supply Router

The supply router is an **offline queueing layer** for already captured bounty
evidence. It exists to keep a fast swarm from repeatedly querying providers
while preventing old snapshots from remaining actionable forever.

It does **not** fetch GitHub, Slack, marketplaces, exchange rates, or wallets.
It does **not** claim a bounty, contact a sponsor, merge external code, or treat
an advertised offer as earned payment. The existing
`concierge.bounty_qualification` gate remains authoritative for canonical
state, reward consistency, contribution-policy blocks, competition saturation,
and unsafe private-context requirements.

## Default routing policy

| Route | Fresh fixed USD evidence | Operational meaning |
| --- | ---: | --- |
| `ACTIVE` | `>= $15` | Eligible for the main work queue after normal collision checks |
| `MAYBE` | `$10 <= reward < $15` | Save only in `#bounty-pile-10-49`; do not consume active build capacity |
| `PRUNE` | `< $10` | Disregard for paid-work dispatch |
| `HOLD` | stale/incomplete/ambiguous or no trustworthy fixed USD floor | Refresh source/terms; do not infer value |

The **$15 ACTIVE / $10 MAYBE floors are fixed owner policy**, not caller
configuration. API calls that attempt to override either floor fail closed, and
the CLI does not expose floor override flags.

The existing `bounty-pile-10-49` queue name remains for compatibility; its current
range is $10 through $14.99. The active floor follows the October 4 owner
instruction for GREEN platforms; routing does not establish payer readiness,
assignment, an award or payment.

A qualification-level `REJECT` is routed to `PRUNE`/suppression. A
qualification-level `HOLD` stays `HOLD`. Fresh otherwise-actionable rows also
pass through `bounty_acceptance_safety_gate`; API-key, session-cookie,
`.env`, private-runtime, hidden-instruction, or private-reasoning disclosure
demands route `HOLD / UNTRUSTED_ACCEPTANCE_TEXT` without persisting source
prose.

RTC and other native-token amounts are intentionally **not converted to USD**.
An RTC-only listing therefore cannot satisfy the USD work floor through this
router. If the operator later captures a canonical fixed-USD contract, route
that new evidence instead of injecting an exchange-rate assumption.

## Freshness contract

Every candidate should carry an offset-aware `observed_at` timestamp identifying
when its canonical issue/reward/competition evidence was captured. Every route
run requires an explicit `evaluated_at` timestamp. Both are normalized to UTC
and bound into the receipt.

The default freshness ceiling is **900 seconds (15 minutes)**:

- age `<= 900` seconds is fresh;
- age `> 900` seconds routes `HOLD / SOURCE_EVIDENCE_EXPIRED`;
- missing observation time routes `HOLD / SOURCE_OBSERVATION_MISSING`;
- a future-dated observation routes `HOLD / SOURCE_OBSERVATION_IN_FUTURE`.

The ceiling is configurable with `--max-age-seconds`, and its exact value is
also receipt-bound. Re-running an unchanged snapshot later therefore changes its
age/receipt and eventually removes it from ACTIVE/MAYBE until source evidence is
refreshed.

## Capture a shortlist once

Save the issues selected by the collector in `shortlist.json`, replacing the
example repository and issue numbers with the actual work pool:

```json
[
  {"repo": "OWNER/REPO", "number": 123},
  {"repo": "OWNER/REPO", "number": 456}
]
```

The collector also accepts `{"candidates": [...]}`. It deduplicates the same
repository and issue before any provider reads, ignoring repository-name case.
The input file is limited to 1 MiB and 1,000 rows; each row contains only `repo`
and `number`.

```bash
python -m concierge.bounty_capture_batch shortlist.json \
  --output-dir capture-run --max-issues 25 --max-pages 10 \
  --max-requests 100 --json

python -m concierge.bounty_supply capture-run/supply.json \
  --evaluated-at CURRENT_OFFSET_AWARE_TIME --json
```

Replace `CURRENT_OFFSET_AWARE_TIME` with the actual current offset-aware
ISO-8601 time, such as `2026-10-04T10:00:00+00:00` when that is the current time.
The first command reads GitHub serially through one shared HTTP session. The
second routes only the saved evidence and makes no provider requests.

`capture-run` must be a new directory. The collector creates it with mode 0700
and saves the canonical `shortlist.json` before provider reads. Each completed
private capture is written with mode 0600 as the run progresses. Its outputs are:

| Output | Use |
| --- | --- |
| `shortlist.json` | Immutable canonical input saved before provider reads |
| Individual capture files | Retain each issue's completed evidence as the run progresses |
| `summary.json` | Safe final manifest of completed work and remaining coverage |
| `supply.json` | Final list of completed captures accepted by the offline supply router |
| `remaining.json` | Final list of failed, incomplete or unattempted issues for the next explicit run |

`--max-issues` bounds the unique issues attempted in this run; excess issues stay
in `remaining.json`. `--max-pages` bounds each paginated traversal, not total
requests: related-PR reads can still require many GETs even at `--max-pages 1`.
`--max-requests` supplies the total dispatch budget. With the CLI's default
Requests session, it counts the initial HTTP GET and each followed redirect hop.
It defaults to 100 and accepts 1–10,000. Exhaustion stops with
`REQUEST_LIMIT` before another call; the current incomplete issue and all
unattempted issues remain in `remaining.json`.

The default CLI session does not configure automatic retries. Python callers
using custom retrying adapters, authentication or response hooks that send
requests themselves, or get-only transports may perform additional attempts
inside one counted call; those internal attempts are outside this counter.
Run `python examples/capture_redirect_budget.py` for the focused loopback check;
it uses real Requests without calling GitHub.

An issue-specific failure does not stop independent candidates. For example,
a missing issue (HTTP 404), a repository-specific HTTP 403 without rate-limit
signals, or a PR mistakenly included in an issue shortlist is recorded as a
failed item, then collection continues. Failed issues remain in `remaining.json`;
completed captures remain available to the supply router. When every candidate
has been attempted but some failed, the partial summary reports `ITEM_ERRORS`.
`attempted_count` and `failed_count` distinguish these failures from unattempted
work. The existing issue and request budgets still apply to all attempts.

Rate limits, HTTP 401 authentication errors, server errors, transport failures,
output failures and interruption still stop the run. Handled failures and Ctrl+C
retain completed captures and write the final summary, supply and remaining files
when storage is available. The collector neither retries automatically nor
schedules a later run. After resolving the recorded failure, resume only the
remaining shortlist into a new directory:

```bash
python -m concierge.bounty_capture_batch capture-run/remaining.json \
  --output-dir capture-run-next --max-issues 25 --max-pages 10 \
  --max-requests 100 --json
```

A hard kill can leave only `shortlist.json` and the individual captures already
written; the final three files may be absent. Recover those completed captures
offline before resuming collection:

```bash
python -m concierge.bounty_capture_recover capture-run \
  --output-dir capture-run-recovered --json
python -m concierge.bounty_capture_batch capture-run-recovered/remaining.json \
  --output-dir capture-run-next --max-issues 25 --max-pages 10 \
  --max-requests 100 --json
```

Recovery reads the saved shortlist and individual `capture-*.json` files,
validates each completed capture with the existing offline replay, and writes
new `supply.json`, `remaining.json` and `summary.json` files. It preserves the
source files, exact recovered capture bytes, observation timestamps and any
submission target. Truncated, invalid or conflicting captures are reported;
their issues remain unfinished while other valid captures can be recovered.
Recovery does not use the old summary, refresh evidence or make provider reads.

Within one recovery invocation, byte-identical captures can reuse a successful
parse and replay after their shortlist identity and submission target match.
The cache keys are the exact file bytes, not a claimed receipt digest or issue
number. It retains at most 1 MiB of raw keys and 128 successful entries, evicting
the least recently used entries. Oversized files, invalid captures and captures
outside the shortlist are not cached. Every file is still read and copied;
changed bytes are validated again, conflicting observations remain errors,
and a new invocation starts without cached validation. No observation time,
qualification, output schema or file protection is changed.

A [bounded hosted comparison](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37190068830)
on October 4, 2026 used the real package and synthetic closed/non-reward captures.
For 100 identical files, replay calls fell from 100 to 1 and median recovery time
from 0.2078 to 0.0512 seconds across five repetitions. For 100 unique issues,
all 100 still replayed (0.2190 versus 0.2165 seconds, three repetitions).
All 12 cases preserved outputs apart from the recovery run's clock fields;
the comparison attempted no network access. These are workload-specific results,
not measured fleet throughput. The [pinned comparison script](https://github.com/woahwhattheheck/bounty-concierge/blob/0fded9a52f272f043142bb7d1650fbc34267fec6/measure_recovery.py)
includes source-blob guards, altered-byte/conflict cases and both cache bounds.

The recovery output directory must be new, and retains the collector's private
directory/file modes. Exit 0 means every issue has a valid recovered capture;
exit 2 means remaining issues, recovery errors or invalid input. Read the
recovery summary before resuming: `request_count` is zero for recovery, while
`captured_request_count` describes only reads recorded in recovered captures,
not failed or interrupted reads from the original run. Continue from the new
`remaining.json` so completed captures do not consume provider capacity again.

Batch exit 0 means the entire shortlist was collected, including any completed
HOLD or REJECT results. Exit 2 means partial collection or invalid input; Ctrl+C
returns 130. Invalid input can be rejected before an output directory is created.
Check `summary.json` before handing off a run. A partial
`supply.json` can route its completed captures, but covers only that subset;
zero ACTIVE rows do not establish an empty work queue.

Captures preserve the final preflight disposition, conditional checks, reduced
assignment evidence, source generations and actual provider-read interval.
Offline replay checks those same decisions before routing. Freshness begins at
the first provider read, and a saved HOLD or REJECT cannot become ACTIVE by
replaying only its baseline inputs. Exporting, resuming or routing does not
refresh an earlier capture's clock; collect it again when its evidence expires.

### Retained pull request identity

New captures retain an optional `baseline.linked_prs` list from the initial
canonical audit's already-fetched PR details. Each entry contains the canonical
PR `repository` and `number`, plus nullable `author_login`, `head_repository`,
`head_ref` and `head_sha`. A same-repository audit row uses the captured issue's
repository; a head fork never substitutes for the PR's repository. Missing
provider fields stay `null`, including a deleted or unavailable head repository.

Replay requires every retained PR identity to match a detail endpoint in the
existing `observation.source_urls`, within the issue or explicit submission
target repository. It rejects malformed or duplicate identities. The existing
capture digest binds this evidence, and the offline replay's routing snapshot
keeps its own copy. Old captures without this list remain supported unchanged;
absence does not establish that no related PR existed.

These fields describe captured source identity, not current ownership, a stable
head throughout collection, contributor eligibility, acceptance or payment.
They do not change the four canonical audit gate fields, qualification, source
age or provider request count. Inspect them in the private capture or replay
snapshot; reduced routing output and safe summary receipts do not expose them.

The capture files and `supply.json` contain source prose needed for replay. Keep
them in trusted custody and share the reduced routing result or safe summary
for coordination. Content digests detect changes but do not authenticate an
untrusted source. Existing assignment, qualification and routing policy remains
in effect; collection completion does not authorize a claim or establish payment.

### Capture one issue

For a single issue, the existing preflight command can write the same evidence:

```bash
python -m concierge.bounty_preflight OWNER/REPO ISSUE_NUMBER \
  --capture issue-capture.json --json
python -m concierge.bounty_supply issue-capture.json \
  --evaluated-at CURRENT_OFFSET_AWARE_TIME --json
```

The parent directory must exist; `--capture` creates a new mode-0600 regular file
and never overwrites an existing path. Single-issue preflight uses different
exit semantics from the batch collector: 0 for ACTIONABLE, 2 for HOLD and 3 for
REJECT. All three completed results can produce a capture; a provider failure
produces no completed capture. Python callers use
`preflight_bounty(..., include_capture=True)` and keep its private `capture`
value out of ordinary logs.

## Input contract

The CLI accepts the native captures produced above, individually or in the
batch collector's `supply.json`. It replays their final preflight checks before
applying the routing policy.

Legacy candidates remain supported as normalized snapshots accepted by
`bounty_qualification.qualify_dispatch`, plus:

- `repo`: canonical `owner/repository` slug;
- `number`: positive issue number;
- `observed_at`: offset-aware ISO-8601 canonical observation time.

Canonical audit fields should include:

```json
{
  "issue_state": "open",
  "open_pr_count": 0,
  "stale_listing_signal": false,
  "search_truncated": false
}
```

The CLI accepts one snapshot, a list of snapshots, or
`{"candidates": [...]}`. It also accepts the retained bounty index export
`{"bounties": [...]}` directly, including `data/bounty_index.json`.
Saved live or offline `browse --report` / `browse --index` JSON reports are
accepted through the existing browse decoder, including its row-count and
coverage checks. Ambiguous `rows` plus `bounties` envelopes are rejected.

Index exports and browse reports contribute their rows only. Collection times
and counts do not supply a row's `observed_at`, canonical audit, or coverage.
The resulting routing counts cover the supplied selection only.
Discovery rows with missing evidence therefore retain the existing HOLD result;
the loader does not promote the export into fresh canonical evidence.

```bash
python -m concierge.bounty_supply supply.json \
  --evaluated-at 2026-09-20T01:00:00Z --json

python -m concierge.bounty_supply supply.json \
  --evaluated-at 2026-09-20T01:00:00Z --max-age-seconds 900

python -m concierge.bounty_supply data/bounty_index.json \
  --evaluated-at 2026-10-03T07:00:00Z --json

concierge browse --index data/bounty_index.json --tier major --limit 5 \
  --report > selected-bounties.json
python -m concierge.bounty_supply selected-bounties.json \
  --evaluated-at CURRENT_OFFSET_AWARE_TIME --json
```

## Deduplication and receipts

GitHub repository names are case-folded for identity, producing a key like
`owner/repo#123`.

Multiple observations for the same issue are compared by their **safe normalized
source semantics**, not by raw prose. If those semantics are identical, the
newest observation is authoritative for freshness and `source_row_count`
records how many generations were collapsed. This prevents an older duplicate
from making a refreshed row stale. Acceptance-safety classification is bound to
the router's trusted `evaluated_at`, so missing/stale/fresh/future clocks of
identical source text cannot mint a false `CONFLICTING_DUPLICATE_EVIDENCE`.

A future-dated newest observation still fails closed rather than falling back to
an older row.

If duplicate generations disagree on safe qualification/economic evidence, the
router **fails closed** with `CONFLICTING_DUPLICATE_EVIDENCE`. A canonical
qualification `REJECT` is never downgraded by a conflicting permissive row:
REJECT conflicts route `PRUNE`; HOLD conflicts stay `HOLD`; otherwise
conflicting ACTIONABLE observations route `HOLD`. Conflict receipts bind the
sorted safe candidate-semantic signatures, so distinct conflict sets cannot
alias to the same receipt. The router never chooses the higher reward or the
more permissive semantic state.

Rows and the aggregate result carry deterministic SHA-256 receipts over
canonical JSON. Input order therefore does not change the output. Receipts bind
safe normalized evidence, observation/evaluation time, freshness age, and policy;
raw issue bodies and comments are never copied into persisted route rows.

## Provider-pressure workflow

1. Collect the selected shortlist once with `bounty_capture_batch`.
2. Check `summary.json` for coverage and retain `remaining.json` for any later run.
3. Route `supply.json` offline with an explicit current `evaluated_at` and share
   the reduced routing results with builders.
4. Work only fresh `ACTIVE` rows after a collision check.
5. Send fresh `MAYBE` rows to the saving pile, not the build queue.
6. When evidence ages out, refresh canonical source instead of replaying old state.
7. Re-capture canonical evidence again before a real claim/submission if the
   source may have changed.

This split keeps source retrieval bounded while queue economics stay cheap,
deterministic, freshness-aware, and reviewable.
