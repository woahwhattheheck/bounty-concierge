# Upstream PR collision guard

tools/upstream_pr_collision_guard.py is the final offline gate before a bounty
publisher creates a new upstream pull request.

It exists for a specific failure mode: a source-complete handoff can remain in
Slack after another contributor, or another fleet publisher, has already opened
a credible carrier for the same canonical issue. Internal claim ownership alone
does not catch that external race.

## Input contract

Fetch the canonical issue immediately before publication. If that issue snapshot
is stale or the canonical issue is no longer open, the guard can stop there:
`pulls` is not required for those blocked outcomes. This lets intake prune dead
marketplace rows after one canonical issue read instead of spending a second
provider request on a PR search.

For a fresh open issue, save JSON with captured_at, issue, pulls, and optionally
submitted_pr_numbers. Open issues remain fail-closed when `pulls` is missing.
The issue object must contain repository (OWNER/REPO), number, and state. Each
pull record should include number, state, merged (or merged_at), title/body, URL,
and its head SHA.

submitted_pr_numbers is useful when a marketplace or issue body names submitted
PRs directly. Pulls may also contain other candidate PRs; the guard recognizes
issue URLs and common Fixes/Closes/Resolves/Refs #N forms.

To catch duplicate carriers created under different child issues, the snapshot
may also include `candidate_files`, and individual pull records may include
`files`. These use the normal GitHub changed-file shape: `filename` (or
`path`) plus the postimage blob `sha` (or `blob_sha`). The guard hashes the
sorted path/blob pairs. A live pull with the same complete fingerprint blocks
publication even when its title/body references another issue.

Fingerprinting is an optional second fence, not a reason to enumerate every
pull request in a busy repository. Under rate pressure, attach file records
already obtained during normal compare/readback or only for a small plausible
candidate set. Missing fingerprints preserve the existing issue/head decision
path; malformed supplied fingerprints fail closed.

If pull discovery returns empty but the subsequent GitHub create call returns
HTTP 422 with the authoritative `A pull request already exists for OWNER:BRANCH`
error, add that normalized response as `create_error` and rerun the guard.
`PROVIDER_COLLISION` is terminal for that create attempt: do not retry the
create, rebuild source, or dispatch another publisher. Resolve the canonical PR
with an exact head/branch read instead. Other 422 validation failures are not
silently treated as collisions and fail closed as malformed reconciliation
input.

## Run

    python3 tools/upstream_pr_collision_guard.py \
      --snapshot /tmp/upstream.json \
      --issue owner/repo#123 \
      --self-head-sha "$HEAD_SHA"

The default freshness limit is 300 seconds and can be tightened with
--max-age-seconds.

Statuses:

- PUBLISH_ALLOWED: fresh issue is open and no live related carrier exists.
- SELF_CARRIER_EXISTS: the exact head already has a live carrier; do not create
  another PR.
- COLLISION: another open or merged related PR exists.
- CONTENT_COLLISION: another open or merged PR has the exact same changed-path
  and postimage-blob fingerprint, even if it references a different child issue.
- PROVIDER_COLLISION: GitHub's create endpoint authoritatively reported that a
  pull request already exists for the requested head. Do not retry creation;
  reconcile that exact head/branch to the canonical PR.
- ISSUE_NOT_OPEN: canonical issue is no longer open.
- STALE_SNAPSHOT: provider state is too old, or implausibly future-dated.

Exit code is 0 only for PUBLISH_ALLOWED, 2 for every safe publication block,
and 1 for malformed or incomplete input.

This is not a provider searcher, claimant lock, payout gate, or scheduler. A
provider-facing worker is still responsible for obtaining the fresh canonical
snapshot. Refresh the internal swarm claim separately; both internal ownership
and external upstream state must be clean before publication.
