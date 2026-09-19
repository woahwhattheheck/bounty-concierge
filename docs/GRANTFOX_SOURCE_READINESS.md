# GrantFox source-readiness receipts

`concierge.grantfox_source_readiness` is a fail-closed compatibility check between
a **verified GrantFox queue receipt** and the source tree a worker is actually
about to reason about.

It exists because issue text can outlive implementation details. A task may name
a file, type, field, or helper that was moved, deleted, consolidated, or replaced
before the worker sees the issue. In a fast bounty swarm, blindly following that
stale text creates duplicate PRs, incorrect implementation plans, and false
"ready to code" signals.

This guard does not fetch GitHub itself and does not mutate GrantFox, GitHub,
wallets, submissions, or payouts. A collector supplies a fresh, immutable source
observation. The compiler validates that evidence, binds it to the canonical
issue identity, and emits a tamper-evident advisory receipt.

## Relationship to the queue gate

The source-readiness request embeds a receipt produced by
`concierge.grantfox_queue_gate`. The embedded receipt must verify successfully,
and its canonical owner/repository must match the repository snapshot.

That means source evidence cannot silently switch a GrantFox card to a different
repository, and a source receipt cannot repair or upgrade a tampered queue
receipt.

The source layer also re-evaluates freshness at its own `evaluated_at` time. A
queue observation that was fresh when first compiled but has aged past its
provider snapshot window is a HOLD here. This prevents an old "apply eligible"
observation from being laundered through a newer code snapshot.

## Input contract

```json
{
  "schema": "grantfox-source-readiness/v1",
  "queue_receipt": {
    "...": "complete verified grantfox-queue-gate/v1 receipt"
  },
  "repository_snapshot": {
    "repository_full_name": "StellarStream-HQ/StellarStream",
    "default_branch": "contributing",
    "commit_sha": "5003c2d22a8dfde3d6a1fe3498300e7f285acf89",
    "observed_at": "2026-09-19T21:41:30Z"
  },
  "expectations": [
    {
      "kind": "path",
      "value": "contracts/Contract-V1/src/types.rs",
      "matches": []
    },
    {
      "kind": "symbol",
      "value": "UserProfile",
      "matches": []
    },
    {
      "kind": "symbol",
      "value": "DataKey::UserStreams",
      "matches": [
        {
          "path": "contracts/Contract-V1/src/storage.rs",
          "blob_sha": "bdcc6ae0f9f797550cab3660d1985ac13fe8a0e5"
        }
      ]
    }
  ],
  "replacements": [
    {
      "for_kind": "symbol",
      "for_value": "UserProfile",
      "replacement_kind": "symbol",
      "replacement_value": "DataKey::UserStreams",
      "evidence": [
        {
          "path": "contracts/Contract-V1/src/storage.rs",
          "blob_sha": "bdcc6ae0f9f797550cab3660d1985ac13fe8a0e5"
        }
      ]
    }
  ],
  "evaluated_at": "2026-09-19T21:42:00Z",
  "max_snapshot_age_seconds": 900
}
```

### Repository snapshot

- `repository_full_name` must match the verified queue receipt identity,
  case-insensitively.
- `commit_sha` is a full 40-character Git commit SHA. Floating branch names are
  not accepted as evidence identity.
- `default_branch` records the branch the collector resolved before pinning the
  commit.
- `observed_at` and the request-level freshness window make source evidence
  explicitly time-bounded.

### Expectations

An expectation records a source reference that the issue or baseline expects to
exist.

- `kind: "path"` requires an exact repository-relative path. It may have zero
  or one match, and any match must use that exact path.
- `kind: "symbol"` may have zero or many matches because a symbol can appear in
  several files.
- Every match binds both its path and full Git blob SHA.
- Duplicate expectations and duplicate match evidence fail closed.
- Absolute paths, parent traversal, dot segments, repeated separators, and
  backslash aliases are rejected.

A zero-length `matches` list means that reference is missing in the pinned
source snapshot.

### Replacements

A replacement explains how a worker has reconciled a missing issue reference
against current source.

Each replacement must:

1. point to a missing expectation already declared in the request;
2. name a replacement path or symbol that is **also declared as a present
   expectation** in the same pinned request; and
3. carry one or more path + blob-SHA evidence records drawn from that replacement
   target expectation's exact normalized matches.

This target-binding rule prevents arbitrary witness bytes from laundering a
missing source reference into `SOURCE_DRIFT_REPLAN`. A path replacement is
therefore bound to evidence for that exact path, while a symbol replacement is
bound to the declared matches for that exact symbol. Unrelated or undeclared
replacement evidence fails closed.

A replacement is **not** permission to implement. It only means the stale
reference has enough pinned evidence for the worker to rewrite the plan.

## Dispositions

### `SOURCE_ALIGNED`

Every declared source expectation has at least one match and both provider/source
snapshots are fresh.

Advisory next action: `USE_PINNED_SOURCE_BASELINE`.

### `SOURCE_DRIFT_REPLAN`

At least one issue reference is absent, but every missing reference has explicit
replacement evidence.

Advisory next action:
`REPLAN_AGAINST_PINNED_SOURCE_BEFORE_APPLICATION_OR_IMPLEMENTATION`.

This is deliberately not called "ready to implement." A queue receipt may still
say `WAIT_ASSIGNMENT`, or the provider may require a new application refresh.

### `HOLD`

The guard holds when:

- provider evidence aged past the queue receipt's freshness window;
- the repository snapshot aged past the source freshness window; or
- one or more missing references have no replacement evidence.

No downstream caller should treat a HOLD as application or implementation
authority.

## StellarStream #1448 example

The motivating source baseline exposed a useful real-world failure mode.

At pinned `StellarStream-HQ/StellarStream@5003c2d22a8dfde3d6a1fe3498300e7f285acf89`:

- the issue-suggested `contracts/Contract-V1/src/types.rs` path does not exist;
- searches find no `UserProfile`, `outgoing_streams`, or `incoming_streams`
  implementation fields;
- current storage defines persistent `DataKey::UserStreams(Address)`;
- public `get_user_streams` is documented as streams associated with a user as
  **sender or receiver**; and
- stream creation appends the same stream id to both sender and receiver with
  `add_user_stream`.

That evidence means two new directional getters cannot safely be implemented as
aliases of the current union index: doing so would return incoming ids as
outgoing and vice versa. The source-readiness guard represents that situation as
drift that requires a pinned-plan rewrite instead of pretending the stale issue
architecture is still current.

## CLI

```bash
python -m concierge.grantfox_source_readiness source.json --json
```

Exit codes:

- `0`: `SOURCE_ALIGNED` or `SOURCE_DRIFT_REPLAN`
- `2`: `HOLD`
- argparse error: malformed or contradictory input

The full JSON receipt contains the embedded verified queue receipt, canonical
issue identity, repository commit, normalized source evidence, missing/replaced
counts, reason codes, authority ceiling, and `source_receipt_sha256`.

`verify_source_readiness_receipt()` reconstructs the source request from the
receipt, re-verifies the embedded queue receipt, recompiles all semantics, and
requires byte-for-byte equality with the supplied receipt.

## Authority ceiling

Every receipt fixes these fields:

```json
{
  "advisory_only": true,
  "provider_application_authority": false,
  "implementation_write_authority": false,
  "submission_authority": false,
  "payment_or_wallet_authority": false
}
```

Source alignment is evidence about code shape. It is never an assignment,
reward, payment, or permission grant.
