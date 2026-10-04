# Shared capture cooldowns

The existing capture command can share GitHub rate-limit deadlines between
invocations. It still collects a bounded shortlist and writes unfinished work
for a later run; it does not sleep, retry, schedule work or cache successful
responses.

## Use the same private file

Create a private directory outside the repository once. Workers using the same
GitHub credential and SQLite file will share observed cooldowns:

```bash
install -d -m 700 "$HOME/.cache/bounty-capture"
python -m concierge.bounty_capture_batch shortlist.json \
  --output-dir /tmp/capture-run-001 \
  --cooldown-file "$HOME/.cache/bounty-capture/github.sqlite" \
  --max-requests 100 --json
```

The output directory must be new, as before. On a quota stop, keep the resulting
`remaining.json` and respect the reported retry interval before a later run.
Omitting `--cooldown-file` leaves the previous per-batch pacing behavior intact
and performs no cooldown-file I/O. Python callers can pass `cooldown_file=` to
`collect_batch` instead. `token` or the configured `GITHUB_TOKEN` defines the
credential scope, matching preflight; callers using custom session credentials
must supply the matching token explicitly.

Before each transport attempt, including redirect hops, the capture reads the
shared deadline. An active deadline produces `RATE_LIMITED` without increasing
`request_count`. The report adds `shared_cooldown.enabled`, `.deferred` and
`.state_error`; raw credentials, credential fingerprints and private paths are
not included. Unfinished candidates remain in the normal output. A successful
response that exhausts the last quota is still returned; its quota evidence
prevents further reads.

Observed Retry-After seconds or HTTP dates are respected. When remaining quota
is zero, the primary reset is also respected, taking the later deadline. A
non-exhausted primary reset does not control a secondary throttle. Without a
usable future deadline, the fallback is 60 seconds. GitHub documents these
provider signals and a minimum one-minute wait for an otherwise unspecified
secondary limit in its [rate-limit guidance](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api#exceeding-the-rate-limit).
This command performs no automatic retries; repeated manual secondary-limit
failures still require increasing backoff rather than a fixed-rate loop.

SQLite transactions only extend a deadline; a concurrent shorter observation
cannot overwrite a longer one. The store contains a one-way credential-scope
fingerprint and an epoch deadline, not tokens, account details or API payloads.
The file's parent must already exist and be private; new files use mode 0600.
An unreadable, locked or malformed requested store stops dispatch with
`COOLDOWN_STATE_ERROR`, rather than silently bypassing it. A failure to persist
an already-observed limit leaves that batch stopped and sets `.state_error`.

## Boundaries

This is opt-in coordination only for workers that can access the **same file
with reliable SQLite locking and synchronized clocks**. Local disk is the
intended deployment. Separate cloud containers with separate files are not
coordinated. Different credentials have separate rows even when GitHub happens
to share their underlying account quota. There is no claim of account-wide or
fleet-wide enforcement. No lock is held over HTTP: requests already dispatched,
or admitted concurrently before a limit is recorded, can still finish. The
helper is not a proactive global concurrency limiter or a publication gate.

## Measured replay

```bash
PYTHONPATH=. python work/throughput/capture-shared-cooldown-20261004/replay.py
```

The real capture/preflight/Requests path was routed to a synthetic loopback HTTP
server returning 429 with Retry-After. Across 25 sequential capture invocations,
default-off behavior made 25 wire GETs; the shared file made 1 GET and deferred
24 invocations. Every invocation retained all 25 unfinished candidates, and
reported request counts matched wire counts. Actual separate-process deadline
updates retained the maximum deadline. The retained results also cover expiry,
credential isolation, ordinary permission errors, malformed state, successful
last-quota reads, and deadline interpretation.

`results.json` alongside the replay identifies the exact source inputs and
runtime. The source bundle was reused, with the current batch transport and
secure-output helper overlaid. This is controlled request-count evidence, not a
live GitHub benchmark, throughput multiplier, complete current-main suite, or
paid bounty result. Existing redirect, shared-HTTP stop and resume-ordering
changes are preserved.
