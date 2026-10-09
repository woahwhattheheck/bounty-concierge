# Reuse issue-comment links before repeating PR searches

A symbol/title search can miss an existing contribution, while searching for `70` can return unrelated PRs advertising $70. Read the target issue's comments and follow a concrete PR link before rebuilding an apparently unclaimed issue. The new command feeds such positive evidence into the existing `GitHubIntakeCache`; it does not introduce another lease, claim, approval gate, provider client or database schema.

## Run

After one reader obtains the issue comments and the referenced PR metadata through the authenticated GitHub connector, retain a normalized capture:

```json
{
  "issue_url": "https://github.com/example/project/issues/70",
  "observed_epoch": 1791526578,
  "comments": [
    {
      "url": "https://github.com/example/project/issues/70#issuecomment-123",
      "body": "Implementation: https://github.com/example/project/pull/129."
    }
  ],
  "pull_requests": [
    {
      "url": "https://github.com/example/project/pull/129",
      "number": 129,
      "state": "open",
      "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "body": "Closes #70"
    }
  ]
}
```

This is an illustrative capture, not a live provider receipt. Populate fields from the actual readback, especially the observation time, state and full head SHA; never refresh the timestamp without refreshing the source. `url`, `number`, `state`, `head_sha` and `body` correspond to the normalized `get_pr_info` output. Comment `url` and `body` correspond to `fetch_issue_comments`. The original bodies stay in the input capture only and are never stored in SQLite or printed by this command.

```sh
python -m concierge.github_comment_carrier capture.json \
  --cache-file /shared/intake.sqlite

# All readers can reuse the existing installed command, without another search:
concierge-intake-cache get example/project 70 \
  --cache-file /shared/intake.sqlite
```

`CONCIERGE_INTAKE_CACHE` can supply the cache path to both commands. The parent directory must already exist. A shared path must actually refer to shared storage: a container-local SQLite file does not synchronize itself to other machines. Share the retained capture through the fleet's existing internal file surfaces when there is no common filesystem.

## Positive-evidence contract

The capture must name a canonical public GitHub issue. A comment URL must belong to that same issue, and its text must link to a same-repository PR. The retained PR must be open, have a matching URL/number and a valid retained head SHA, and its body must contain a standalone `Closes #70`, `Fixes owner/repo#70` or `Resolves https://github.com/owner/repo/issues/70` line for the exact target. Corresponding close/fix/resolve inflections and case differences are accepted. Quoted or fenced examples do not count. Plain monetary amounts or a different issue number do not count.

The supported standalone closing-reference form is deliberately narrower than all of GitHub Markdown. A non-matching form is unknown, not proof that a contribution is absent. Cross-repository links and closed PRs are not automatically cached by this command; inspect them directly when relevant. Multiple matching PRs or conflicting readbacks are surfaced as rejected input rather than choosing one silently.

A successful match writes the existing `CARRIER` observation with source `github`, reason `ISSUE_COMMENT_AND_CLOSING_REFERENCE`, target, PR number, head and original observation time. The default TTL is 30 minutes; `--ttl` accepts the cache's existing 60-second to 7-day range. Future or expired captures are rejected, and importing a capture does not extend its lifetime. The cache's newer-observation guard remains in force.

## Outputs and boundaries

- `CARRIER_OBSERVED`: the positive target/head observation was stored; exit 0.
- `NEWER_OBSERVATION_RETAINED`: a newer cache observation wins; its record is returned, and the older capture is not reported as written; exit 0.
- `NO_CONFIRMED_CARRIER`: nothing positively matched; no cache write, no negative entry, no absence claim; exit 2.
- `CAPTURE_REJECTED`: malformed, conflicting, future or expired input, or unavailable storage; no raw input/provider text in the error; exit 2.

This is an offline ingestion command. It does not authenticate caller-supplied JSON, independently verify GitHub provenance, fetch a missing page, establish exhaustive search coverage, inspect code correctness, infer bounty eligibility, approve a payment or reserve work for an agent. A cached open PR is a pointer for direct inspection, not proof its author is actively working. Refresh the exact PR's state/head before modifying it; do not re-enumerate the whole repository merely to rediscover its URL.

The CLI caps capture input at 2 MiB, 100 comments and 128 PR readbacks. No extra HTTP requests or dependencies are introduced. Existing cash-admission, MOVA and provider-backoff policies are unchanged.

## Focused execution

```sh
python -m unittest discover -s tests -p test_github_comment_carrier.py -v
```

Three tests execute real SQLite ingestion, expiration and newer-record preservation; prevent monetary/quoted/ambiguous/absent-search false matches; and run this CLI followed by the existing intake-cache CLI. They do not invoke the network or a broad repository suite.
