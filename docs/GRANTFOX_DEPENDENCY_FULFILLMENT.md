# GrantFox dependency fulfillment

`concierge.grantfox_dependency_fulfillment` is the strict landed-capability layer
that follows `grantfox_dependency_readiness/v1`.

The distinction is intentional:

- **issue closure** is coordination state;
- **capability fulfillment** requires evidence that prerequisite code landed on the
  repository's default-branch history.

A prerequisite can be closed as duplicate, not planned, stale, or abandoned.
Therefore a `DEPENDENCIES_CLEAR` receipt from the original closure-level compiler is
not sufficient evidence that prerequisite capability exists.

## Input

The compiler consumes a fully verified dependency-readiness receipt plus one fresh
landing record for every declared prerequisite issue.

Each landing binds:

- same `repository_full_name`;
- exact declared prerequisite `issue_number`;
- `evidence_kind`: `merged_pull_request` or `default_branch_commit`;
- canonical GitHub PR/commit URL;
- landed 40-hex commit SHA;
- observed default branch name and observed default-branch head SHA;
- `contains_landed_commit: true`, representing the caller's default-branch ancestry
  observation;
- fresh UTC observation time;
- bounded human-readable basis.

The compiler does not fetch GitHub and does not pretend to independently prove the
caller's ancestry observation. Its job is to make the required evidence explicit,
complete, tamper-evident, and impossible to replace with `state == "closed"`.

## Dispositions

### `DEPENDENCIES_FULFILLED`

The embedded dependency receipt verifies and is `DEPENDENCIES_CLEAR`; every declared
prerequisite has fresh, exact landed-capability evidence.

Advisory next action: `CONTINUE_WITH_PROVIDER_AND_ASSIGNMENT_GATES`.

This still grants **zero** provider, application, implementation, submission, wallet,
payment, or reward authority.

### `FULFILLMENT_WAIT`

The closure-level receipt is clear but one or more declared prerequisites lack
landing evidence.

Advisory next action: `PROVE_PREREQUISITE_CAPABILITY_LANDED`.

### `HOLD`

The compiler holds if the embedded dependency receipt is not clear or landing
evidence is stale. Contradictory evidence is rejected as invalid input.

## Why this is separate from v1

The original v1 receipt is already merged and may have consumers. Reinterpreting an
existing v1 digest/schema after the fact would make old receipts unverifiable or
silently change semantics. This layer preserves v1 as closure-level evidence while
making the stronger requirement explicit for downstream activation decisions.

Downstream code must not equate `DEPENDENCIES_CLEAR` with capability fulfillment.
When capability landing matters, consume a verified
`grantfox-dependency-fulfillment-receipt/v1` and require
`DEPENDENCIES_FULFILLED`.

## Tamper evidence

The fulfillment receipt embeds the complete verified dependency receipt, normalized
landing evidence, missing issue numbers, reason codes, authority ceiling, and
`fulfillment_receipt_sha256`.

`verify_dependency_fulfillment_receipt()` reconstructs the compiler input, re-verifies
the dependency receipt, recompiles all semantics, and requires exact equality.

## CLI

```bash
python -m concierge.grantfox_dependency_fulfillment fulfillment.json --json
```

Exit codes:

- `0`: `DEPENDENCIES_FULFILLED`
- `2`: `FULFILLMENT_WAIT` or `HOLD`
- argparse error: malformed, contradictory, stale-identity, or tampered input

## Zero-prerequisite issues

A verified dependency-readiness receipt with explicit `dependencies: []` is a complete statement that the issue has no declared prerequisites. The fulfillment compiler therefore accepts `landings: []` for that exact case and emits `DEPENDENCIES_FULFILLED`; it never treats a missing dependency receipt as implicit clearance.
