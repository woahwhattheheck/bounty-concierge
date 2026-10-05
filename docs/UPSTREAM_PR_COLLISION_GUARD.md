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
- ISSUE_NOT_OPEN: canonical issue is no longer open.
- STALE_SNAPSHOT: provider state is too old, or implausibly future-dated.

Exit code is 0 only for PUBLISH_ALLOWED, 2 for every safe publication block,
and 1 for malformed or incomplete input.

This is not a provider searcher, claimant lock, payout gate, or scheduler. A
provider-facing worker is still responsible for obtaining the fresh canonical
snapshot. Refresh the internal swarm claim separately; both internal ownership
and external upstream state must be clean before publication.
