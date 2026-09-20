# Swarm Custody Ledger

`tools/swarm_custody.py` turns high-volume coordination events into a deterministic
custody report. It is intentionally **provider-neutral**: it does not call Slack,
GitHub, GrantFox, a wallet, or any other network service. The caller is responsible
for exporting coordination events; the tool only derives state from those bytes.

## Why this exists

A large swarm can generate several `TAKE` messages per minute. Human tail-reading is
not enough to establish that a work item has exactly one current owner, that a
previous owner really released it, or that a session vanished without completing its
handoff. The ledger gives publication systems one strict artifact to compare before
claiming work or declaring a collision.

It does **not** prove provider assignment or reward eligibility. A `TAKE` is swarm
custody evidence only.

## Input format

Input is UTF-8 NDJSON. Blank lines are ignored. Every non-blank line is one version-1
event with no undeclared fields. Parsing is strict: duplicate JSON object keys and
non-finite constants (NaN, Infinity, and -Infinity) are rejected rather than
silently normalized:

```json
{"version":1,"event_id":"evt-001","work_key":"GFOX2-20260919-087/R","kind":"TAKE","owner":"ZZ-Sol-17","at":"2026-09-19T17:48:21-04:00","artifact":"slack:1789854501.851079"}
```

Required fields are `version`, `event_id`, `work_key`, `kind`, `owner`, and `at`.
`artifact` is optional. Supported kinds are:

- `TAKE` -- acquire live swarm custody. A second owner may also TAKE; that is reported
  as a collision rather than silently choosing a winner.
- `PROGRESS` -- refresh the lease for an owner that already holds a live TAKE.
- `RELEASE` -- relinquish that owner's live TAKE without completing the work.
- `DONE` -- complete the work. DONE is accepted only from the sole live owner and is
  terminal for that `work_key`.

Timestamps must carry an explicit offset. Event order is canonicalized by UTC
timestamp and then `event_id`, so report bytes and receipts do not depend on input
line order. Duplicate event IDs, unknown fields, orphan progress/release events,
ambiguous completion, or events after DONE fail closed.

## Deterministic lease evaluation

There is no implicit current clock. Every run supplies both the evaluation timestamp
and lease duration:

```bash
python tools/swarm_custody.py events.ndjson \
  --as-of 2026-09-19T18:00:00-04:00 \
  --lease-seconds 3600 \
  --pretty
```

A single live claim is `STALE` only when its age is **greater than** the lease.
Exactly at the lease boundary it remains `ACTIVE`. An event later than `--as-of` is
rejected instead of being guessed around.

Dispositions are `OPEN`, `ACTIVE`, `COLLISION`, `STALE`, `DONE`, and `RELEASED`.
`COLLISION` has priority over staleness: two live owners remain a collision even if
both leases are old, because stale time alone does not authorize one owner to erase
another.

`OPEN` is reserved for a known work record with no custody transition; current
version-1 NDJSON is event-driven, so ordinary event streams produce the other five
states.

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | Valid ledger; no live collision and no disallowed stale custody |
| `2` | Invalid input or invalid state transition |
| `3` | One or more live ownership collisions |
| `4` | One or more stale single-owner claims |

`--allow-stale` changes only exit code 4 to success. It never suppresses collision
exit code 3 and never rewrites the report.

## Receipt

The report contains all normalized events, per-work history, active claims,
disposition counts, the caller-supplied lease inputs, and `receipt_sha256`.
The receipt is SHA-256 of the report body before the receipt field, encoded as
sorted-key compact UTF-8 JSON. Reordering equivalent input lines therefore preserves
the receipt.

## Safety and authority boundary

This tool is coordination evidence only. It cannot:

- assign a GrantFox/provider issue;
- submit an application, claim, PR, Slack message, or payment action;
- decide reward eligibility or a payout amount;
- transfer wallet funds;
- choose a human or provider winner when two owners collide.

A publication controller may use its non-zero exit codes as a gate before a separate,
authorized connector action. That controller remains responsible for checking live
provider state, repository permissions, and the current Slack/GitHub record.

## Tests

The stdlib test suite covers canonical permutation invariance, collision priority,
the exact stale boundary, `--allow-stale`, DONE versus RELEASE semantics, duplicate
event IDs, duplicate JSON object keys, non-finite JSON constants, unknown/orphan
events, terminal completion, and machine-readable invalid input:

```bash
python -m unittest -v tests/test_swarm_custody.py
python -m py_compile tools/swarm_custody.py tests/test_swarm_custody.py
```
