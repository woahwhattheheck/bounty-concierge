# Canonical supply filter

`tools/canonical_supply_filter.py` is the offline second-stage gate for bounty leads.
It consumes a caller-supplied snapshot of canonical issue/reward/ownership state and
classifies every row without making network calls or provider mutations.

## Why this exists

Marketplace and discovery feeds can lag canonical GitHub/provider state. A public
card can still look buildable after an issue closed, a reward was consumed, another
worker was assigned, a claim landed, a same-scope PR already exists, the repository
was archived, or a repository-wide bounty total was mistaken for an issue reward.

The filter turns that evidence into one of five explicit decisions:

- `BUILD_ALLOWED`: snapshot is fresh, issue/reward are open, and no active owner exists.
- `CONTINUE_OWNED`: the requested owner already has the assignment, active claim, or
  open same-scope PR.
- `OCCUPIED`: another owner has an assignment, active claim, or open same-scope PR.
- `PRUNE`: the issue/reward is terminal, the repository is archived, or a same-scope PR is already merged.
- `VERIFY_REQUIRED`: evidence is stale, incomplete, malformed, uses an unknown state,
  or says the advertised reward is not issue-specific.

`build_allowed=true` is emitted for both `BUILD_ALLOWED` and
`CONTINUE_OWNED`; `new_build_allowed=true` is emitted only for
`BUILD_ALLOWED`.

## Snapshot contract

The input must use `canonical-supply-snapshot/v1`:

```json
{
  "schema": "canonical-supply-snapshot/v1",
  "captured_at": "2026-10-06T19:15:00Z",
  "items": [
    {
      "resource": "owner/repo#123",
      "issue_state": "open",
      "reward_state": "available",
      "repository_archived": false,
      "reward_scope": "issue",
      "assignees": [],
      "claims": [],
      "same_scope_prs": []
    }
  ]
}
```

Each item must explicitly include all three ownership collections. Claims use
`{"owner":"login","status":"active"}`; same-scope PRs use
`{"owner":"login","state":"open"}`. Populate `same_scope_prs` with all known
same-scope upstream PRs, not only PRs opened by the current owner. Unknown/missing
collection or state values fail closed to `VERIFY_REQUIRED` instead of
accidentally authorizing work.

Two additive canonical-context fields are optional for backward compatibility with
existing v1 snapshot producers, but new collectors should populate them whenever
the evidence is available:

- `repository_archived: true` immediately returns `PRUNE`; `false` permits the
  remaining gates to decide. A present non-boolean value returns `VERIFY_REQUIRED`.
- `reward_scope: "issue"` means the advertised reward applies to this specific
  issue. Any other present non-empty scope (for example `"repository_total"`)
  returns `VERIFY_REQUIRED`, because a repo-wide or meta bounty amount must not
  authorize an issue build. A present empty/non-string value also fails closed.

Omitting either additive field preserves R1 behavior; absence is therefore
compatibility, not affirmative evidence that a repository is writable or that a
reward is issue-specific.

Recognized terminal issue states are `closed`, `deleted`, and `not_found`.
Recognized consumed reward states are `consumed`, `awarded`, `paid`, and
`closed`. A merged same-scope PR is also terminal supply.

## Canonical-target duplicates (batch-safe)

The `resource` field is the canonical GitHub issue identity `owner/repo#number`,
not a marketplace listing ID. A single GitHub issue may appear as five separate
Algora cards or under mixed-case/leading-zero aliases. The batch gate normalizes
these aliases before dispatch: identical canonical evidence keeps the original
decision for the first occurrence and marks every subsequent card `PRUNE`
with `duplicate_canonical_target` and `canonical_primary_row`. This prevents
counting one issue or reward five times and prevents duplicate BUILD allocations.

If duplicate cards disagree on issue/reward status, archive/scope, ownership
collections, or an explicitly provided reward amount/currency, **every alias**
becomes `VERIFY_REQUIRED` with `conflicting_duplicate_canonical_target`;
the input order does not decide which conflicting state to trust. Stale
snapshots still return `VERIFY_REQUIRED` for every row. Missing/invalid
`resource` values retain v1 compatibility but cannot benefit from cross-row
deduplication; collectors should always supply canonical resource IDs.


## Usage

```bash
python tools/canonical_supply_filter.py \
  --snapshot canonical-snapshot.json \
  --owner woahwhattheheck \
  --max-age-seconds 900 \
  --output canonical-decisions.json
```

Use `--now` with a timezone-aware ISO timestamp for deterministic offline replay.

A stale snapshot never authorizes work; every row becomes `VERIFY_REQUIRED`.
The default freshness window is 15 minutes.

## Composition

The WarpSpeed public intake remains a discovery-only first stage. Its
`CLAIM_GATE` rows should be reconciled against fresh canonical evidence, then
passed through this filter before dispatch. The caller is responsible for
collecting that canonical snapshot and for any platform-specific account,
maintainer-assignment, or payment-policy checks.

This tool intentionally does **not** fetch GitHub, claim rewards, create accounts,
open PRs, contact maintainers, or submit work.
