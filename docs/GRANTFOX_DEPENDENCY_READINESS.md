# GrantFox dependency-readiness receipts

`concierge.grantfox_dependency_readiness` closes a gap between **source readiness**
and **assignment-dependent implementation**: an issue can point at valid current
code while still explicitly depending on prerequisite work that has not landed.

The motivating case was Gryd-lock `grydlock-testkit` issue #33. Its source files
exist, but the issue says to build on referential-integrity and
`describe-transactions` tooling. Those prerequisite issues were still open on
the pinned baseline. Treating #33 as implementation-ready would duplicate the
decode layer and create competing PRs.

This guard is advisory-only. It does not fetch GitHub itself and does not mutate
GrantFox, GitHub, submissions, wallets, payouts, or source.

## Input

The compiler consumes:

1. a complete verified `grantfox-source-readiness-receipt/v1`;
2. one or more same-repository prerequisite issue observations;
3. a receipt evaluation timestamp and freshness window.

```json
{
  "schema": "grantfox-dependency-readiness/v1",
  "source_receipt": {
    "...": "complete verified source-readiness receipt"
  },
  "dependencies": [
    {
      "repository_full_name": "Gryd-lock/grydlock-testkit",
      "issue_number": 27,
      "issue_url": "https://github.com/Gryd-lock/grydlock-testkit/issues/27",
      "state": "open",
      "observed_at": "2026-09-19T22:01:00Z",
      "basis": "Issue #33 explicitly says to build on describe-transactions tooling."
    }
  ],
  "evaluated_at": "2026-09-19T22:02:00Z",
  "max_snapshot_age_seconds": 900
}
```

Every dependency must use the source receipt's owner/repository, exact canonical
GitHub issue URL, a distinct positive issue number, `open` or `closed` state, a
fresh observation time, and a non-empty `basis` explaining why it is a real
prerequisite. Duplicate issues, self-dependencies, cross-repository aliases, and
non-canonical URLs fail closed.

The same-repository restriction is intentional for v1. Cross-repository
dependencies require a stronger identity and source-binding contract rather than
silently trusting a URL string.

## Dispositions

### `DEPENDENCIES_CLEAR`

All prerequisite observations are fresh and closed, and the embedded source
receipt is `SOURCE_ALIGNED`.

Advisory next action:
`CONTINUE_WITH_PROVIDER_AND_ASSIGNMENT_GATES`.

This is **not** implementation authority. Provider assignment and every other
write gate still apply.

### `DEPENDENCY_WAIT`

The source is aligned and fresh, but one or more prerequisites remain open.

Advisory next action:
`WAIT_FOR_PREREQUISITES_BEFORE_ASSIGNMENT_DEPENDENT_IMPLEMENTATION`.

The receipt lists exact open prerequisite issue numbers so workers can coordinate
on the blockers instead of duplicating downstream work.

### `HOLD`

The guard holds when the source receipt is not `SOURCE_ALIGNED` or any
prerequisite observation is stale. Source problems take precedence over
dependency state; closed prerequisites cannot launder a source HOLD into a green
signal.

## Tamper evidence

The receipt embeds the complete source receipt, normalized dependency evidence,
reason codes, counts, authority ceiling, and a
`dependency_receipt_sha256`. `verify_dependency_readiness_receipt()` reconstructs
the original request, re-verifies the source receipt, recompiles all semantics,
and requires exact equality.

## CLI

```bash
python -m concierge.grantfox_dependency_readiness dependency.json --json
```

Exit codes:

- `0`: `DEPENDENCIES_CLEAR`
- `2`: `DEPENDENCY_WAIT` or `HOLD`
- argparse error: malformed, contradictory, or identity-mismatched input

## Authority ceiling

Every receipt hard-codes:

```json
{
  "advisory_only": true,
  "provider_application_authority": false,
  "implementation_write_authority": false,
  "submission_authority": false,
  "payment_or_wallet_authority": false
}
```

Dependency clearance says only that declared prerequisite issue observations are
closed and fresh. It never assigns a bounty, proves a reward, authorizes a
provider application, or authorizes source/payment mutation.
