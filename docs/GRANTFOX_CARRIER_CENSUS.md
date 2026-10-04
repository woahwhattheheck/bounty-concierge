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

## Closed-carrier classification cost

The compiler classifies each normalized closed carrier directly from its `process_disposition`. It previously searched the `process_closed` list of dictionaries for every closed carrier. For `n` relevant carriers and `p` process-closed carriers, that step could require `O(n * p)` dictionary comparisons; direct classification takes `O(n)` field checks. The existing 100-observation limit remains in force.

On 2026-10-04, the complete production compiler from source blob `36bfe37ba3a40b5f241982ab85029ffeddac540e` and the version with only this expression changed were executed under CPython 3.12.14. Complete receipts, including their SHA-256 digests, were identical for 10 mixed closed carriers, 100 mixed closed carriers, 100 process-closed carriers, and 100 active carriers. The mixed cases alternated `closed_unassigned` and `normal`; all observations referenced the same issue. Both versions left the input requests unchanged, and the receipt verifier accepted the original receipts. The execution exited successfully.

Repeated full-compiler wall-clock samples were inconclusive because of scheduling variation. A CPU-time follow-up did not return a result before the executor became unavailable. This change therefore claims the reduced classification complexity and preserved observed behavior; it does not claim a measured end-to-end or fleet throughput improvement.
