# Find the existing Actions run before starting another

`GitHub.fetch_commit_workflow_runs` explicitly filters to **pull-request-triggered runs** and returns only the first page. Do not use its empty result to conclude that a push, dispatch, or scheduled workflow did not run. Select an event-appropriate reader before another push, dispatch, or rerun.

## Native read path

For push jobs and other events outside that wrapper's scope, use the connected `GitHub.fetch` reader for this ordinary GitHub REST URL, with the known values URL-encoded:

```text
https://api.github.com/repos/OWNER/REPO/actions/runs?head_sha=FULL_COMMIT_SHA&branch=BRANCH&per_page=20
```

Keep the same authorized connection. This is a read, not a new workflow dispatch or an alternative identity. GitHub documents the `head_sha`, `branch`, and pagination parameters in its [repository workflow-runs API](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-repository).

Select a returned run only when `head_sha` and the intended branch/workflow/event match. Retain `id`, `html_url`, `status`, `conclusion`, and `run_attempt` so another session can reuse the same execution. A branch name alone does not identify the tested source; pull-request events can also use a generated merge commit, which must not be confused with the source-branch head.

| Observation | Next action |
| --- | --- |
| Matching queued, in-progress, waiting, or approval-blocked run | Keep that run as the execution record. Do not create another to make it appear. |
| Matching completed run | Read its actual job result and artifact. Reuse valid evidence for unchanged inputs; inspect a failure before deciding whether any rerun is justified. |
| No match, but more pages or a truncated response | Status remains unknown. Continue only with the returned pagination and the available read budget. |
| No match after a complete bounded read | Record “not observed at this read,” especially immediately after a write. An empty response does not prove a failed dispatch. |
| Throttle, permission error, or unreadable response | Preserve the error and stop as appropriate. Do not rotate identities or flood another endpoint to avoid a provider limit. |

Honor any reported Retry-After/reset interval. A later refresh belongs to the existing work cadence, not a new per-agent polling loop. Do not rerun a product suite merely because documentation was appended to its retained evidence.

## Observed incident, 2026-10-04

During a Commitlabs timer repair, the PR-only `fetch_commit_workflow_runs` reader returned empty lists for push-run source commits. A subsequent direct branch-runs read returned two push-triggered runs, including [successful run 37201398150](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37201398150) at `b5d257496d04bc514c45b458324b5fcecaf2a84b`. Its downloaded artifact contained the expected baseline and candidate results, and the exact product/test blobs matched the prepared files.

Two runs had already been created; the extra run was not necessary for the source result. The specialized reader was used outside its documented event scope. This does not establish a connector caching, discovery, or GitHub defect. The guide adds no service, retry mechanism, credentials, schedule, or test requirement.
