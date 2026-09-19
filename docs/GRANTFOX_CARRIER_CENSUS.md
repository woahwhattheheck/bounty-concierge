# GrantFox carrier census

`concierge.grantfox_carrier_census` is an advisory, fail-closed pre-queue control for deciding whether an open GrantFox issue is actually fresh work.

It exists because issue-open/unassigned state is not enough: a pull request may already implement the issue, or a strong carrier may have been closed only because it was opened before maintainer assignment. Feeding either case into the fleet as greenfield work wastes application and implementation capacity.

## Contract

Input schema: `grantfox-carrier-census/v1`.

For one canonical GitHub issue, supply a fresh list of same-repository PR observations. Every observation records:

- canonical `pr_url`;
- current `state`: `open`, `closed`, or `merged`;
- `issue_relation`: `closes`, `references`, or `none`;
- `process_disposition`: `normal`, `closed_unassigned`, `superseded`, or `unknown`;
- optional exact 40-hex `head_sha`.

The compiler emits a deterministic, SHA-256-bound receipt with one of these advisory dispositions:

- `CLEAR_FOR_QUEUE_EVALUATION` — no relevant carrier was observed; continue to the existing provider/source gates.
- `REUSE_EXISTING_CARRIER` — an open or merged issue-related carrier exists; do not start a duplicate build.
- `REAPPLY_WITH_REUSABLE_CARRIER` — an issue-related carrier was closed specifically because work started without assignment; obtain assignment before reusing/rebasing it.
- `REVIEW_CLOSED_CARRIER` — a relevant closed carrier exists but its closure semantics need review.
- `HOLD` — census evidence is stale.

The receipt never grants provider-application, implementation-write, submission, wallet, reward, or payment authority.

## Why this is separate from `grantfox_queue_gate`

`grantfox_queue_gate` already fails closed when `linked_pr_urls` are supplied. Carrier census is the evidence-normalization step that determines whether candidate PR observations are actually same-repository, issue-related carriers and distinguishes active/merged carriers from process-closed reusable work. Its output is intended to feed fleet intake before a worker treats an issue as fresh.

## Hostile cases

The module rejects URL aliases, encoded paths, queries/fragments, userinfo, ports, cross-repository PRs, duplicate PR identities, unsupported fields, inconsistent `closed_unassigned` state, stale snapshots, and receipt tampering.

## CLI

```bash
python -m concierge.grantfox_carrier_census snapshot.json --json
```

Exit code `0` means only `CLEAR_FOR_QUEUE_EVALUATION`. All suppression/review/hold dispositions exit `2` so shell-based intake can fail closed.
