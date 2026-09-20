# Bounty deadline gate

`concierge.bounty_deadline_gate` is an **advisory-only** fail-closed gate for a common bounty-market failure mode: an issue or provider row can remain `OPEN`, unassigned, and visibly priced after the sponsor's own deadline has elapsed.

The gate does not decide whether a bounty is worth doing. It decides only whether frozen deadline evidence is current enough to be handed to separate economics, carrier/collision, assignment, and write-authority gates.

## Why this is separate from the economics gate

A verified `$750` or `$1,200` amount does not prove that an expired offer can still be claimed. Likewise, an open GitHub issue or provider `status: open` label is not an extension of the sponsor's deadline.

A concrete fleet failure case on 2026-09-19 was a pair of automatically synced Owockibot issues that still said `open` while their own issue bodies named deadlines of 2026-08-31 and 2026-09-16. The correct default is HOLD until the sponsor or official provider supplies a fresh extension.

## Input contract

```json
{
  "schema": "bounty-deadline-gate/v1",
  "issue": {
    "url": "https://github.com/example/project/issues/5",
    "state": "OPEN",
    "observed_at": "2026-09-19T23:50:00Z",
    "source_content_sha256": "<sha256-of-frozen-issue-observation>"
  },
  "deadline_evidence": {
    "kind": "DATE",
    "value": "2026-09-16",
    "authority": "ISSUE_BODY",
    "source_url": "https://github.com/example/project/issues/5",
    "source_content_sha256": "<sha256-of-frozen-deadline-source>",
    "observed_at": "2026-09-19T23:50:00Z"
  },
  "extension_evidence": null,
  "evaluated_at": "2026-09-19T23:55:00Z",
  "max_observation_age_seconds": 900
}
```

All source bytes are gathered outside this module. The gate makes no network calls. `source_content_sha256` fields bind the receipt to the exact frozen observations supplied by the caller.

### Deadline precision

- `DATE` uses canonical `YYYY-MM-DD`. If evaluation is on a later UTC date, the deadline is definitely elapsed. If it is on the same UTC date, the gate HOLDs with `DATE_BOUNDARY_CLOCK_AMBIGUOUS`; it refuses to invent a sponsor timezone or cutoff time.
- `INSTANT` uses exact UTC RFC3339 ending in `Z` and can be evaluated exactly, including the boundary instant.

An extension must use the **same precision** as the base deadline. Cross-precision comparison would require time-zone assumptions and is therefore rejected.

### Extension authority

Base deadline evidence may come from `ISSUE_BODY`, `OFFICIAL_PROVIDER`, `REPO_OWNER`, or `REPO_MEMBER`.

An extension is stronger: it must come from `OFFICIAL_PROVIDER`, `REPO_OWNER`, or `REPO_MEMBER`, be freshly observed, not predate the base observation, and move the deadline strictly later at the same precision. A stale `OPEN` label, third-party comment, applicant statement, or unchanged issue body does not extend an expired offer.

## Dispositions

- `DEADLINE_CURRENT`: the issue is open, all observations are fresh, and the effective deadline is unambiguously current. This **only** passes the candidate to other gates.
- `HOLD`: closed issue, stale evidence, elapsed deadline, ambiguous date boundary, or invalid/stale extension semantics.

For elapsed offers, the advisory next action is `REQUIRE_EXPLICIT_SPONSOR_EXTENSION_BEFORE_LABOR`.

## Authority boundary

Every receipt permanently records all of these as false:

- provider application authority;
- repository write authority;
- submission authority;
- reward-award authority;
- payment or wallet authority.

Passing this gate never authorizes a claim, implementation, PR, sponsor message, payment request, or wallet action.

## Tamper evidence and semantic verification

Receipts use canonical JSON SHA-256. `verify_receipt(..., semantic=True)` (the default) reconstructs the original request from the receipt and recompiles the state machine, so a caller cannot change `HOLD` to `DEADLINE_CURRENT`, rehash the JSON, and pass verification.

## CLI

```bash
python -m concierge.bounty_deadline_gate compile snapshot.json --json
python -m concierge.bounty_deadline_gate verify receipt.json
```

`compile` exits `0` for `DEADLINE_CURRENT`, `2` for `HOLD`, and parser/input errors are non-zero. `verify` exits `0` only for a semantically valid receipt.

## Test contract

Focused tests cover:

- elapsed deadline despite `OPEN` state;
- date-only boundary ambiguity;
- exact instant boundary;
- valid sponsor extension;
- weak-authority, retrograde, cross-precision, stale, and future-observed extensions;
- stale issue/deadline observations;
- closed issues;
- hostile URL aliases and unknown-field smuggling;
- deterministic receipt hashes;
- rehashed semantic forgery and authority forgery;
- CLI compile/verify and HOLD exit behavior.
