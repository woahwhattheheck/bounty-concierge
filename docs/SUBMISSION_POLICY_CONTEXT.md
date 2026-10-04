# Submission policy source context

Before building a bounty, inspect the contribution terms of the repository that
will receive the submission. A bounty listing, an internal fork and the actual
submission repository can be different. This optional collector records source
documents and the repository permissions reported by the same GitHub connection;
it does not decide eligibility, payment readiness or dispatch.

## Export with preflight

Supply the actual target explicitly. The output parent directories must already
exist, and both output files must be new, distinct paths:

```sh
python -m concierge.bounty_preflight LISTING_OWNER/LISTING_REPO ISSUE_NUMBER \
  --capture /private/issue-capture.json \
  --submission-repo SUBMISSION_OWNER/SUBMISSION_REPO \
  --submission-policy-context-out /private/submission-policy-context.json \
  --json
```

The two policy options must be supplied together. Without them, preflight makes
no policy reads. When requested, the collector reuses preflight's connection pool
after its existing capture interval. Capture v1, qualification and exit status
are unchanged. Policy text goes only into the separate mode-0600 sidecar; the
ordinary JSON summary contains document status, hashes and source URLs without
text. Known-invalid or conflicting output paths fail before provider reads;
final creation remains exclusive and refuses symlink traversal or overwrite.

## Read one target independently

```sh
python -m concierge.submission_policy_context SUBMISSION_OWNER/SUBMISSION_REPO \
  --out /private/submission-policy-context.json
```

Live collection uses `GITHUB_TOKEN` when provided. It performs at most six GETs:
repository metadata, the default-branch reference, then these four exact paths
at the returned commit SHA:

| Path | Purpose |
| --- | --- |
| `CONTRIBUTING.md` | Root contribution terms |
| `.github/CONTRIBUTING.md` | GitHub contribution terms |
| `LLM_USAGE_POLICY.md` | Root model-use terms |
| `.github/LLM_USAGE_POLICY.md` | GitHub model-use terms |

This is a bounded source sample, not an exhaustive policy search. Each document
is limited to 256 KiB of decoded UTF-8 content. Inspect linked or differently
named terms separately if the retained sources require it. A successful CLI run
means the requested observations completed, not that a submission is permitted.

The `submission-policy-context/v1` sidecar records the explicit target,
`snapshot_sha`, observation time, observed permission booleans, requested paths,
pinned source URLs, text and SHA-256 content digests. A native Git blob SHA is
verified and retained when the provider reports one. Missing permissions remain
unknown; the collector does not infer write access from a login or a fork.

| Status | Meaning |
| --- | --- |
| `READ` | The source was retrieved and its content decoded |
| `NOT_FOUND` | The provider reported 404 for that path |
| `DENIED` | The provider denied the read, such as 401 or ordinary 403 |
| `NOT_READ` | No observation was obtained for that requested path |
| `UNAVAILABLE` | Transport failure or an invalid/unusable readback |
| `RATE_LIMITED` | The response contained explicit rate-limit evidence |

A rate-limit response, an observed zero remaining quota, or a transport failure
stops further reads in that collection. Remaining paths stay `NOT_READ`; remote
exception messages are not retained. There are no sleeps, retries or background
reads. The standalone CLI returns 0 when the snapshot and all requested document
observations complete (`READ` or `NOT_FOUND`), otherwise 2. A preflight policy
read failure remains advisory and does not change its existing qualification.

## Reuse retained provider observations

Existing connector readbacks can use the same reader without network access:

```sh
python -m concierge.submission_policy_context SUBMISSION_OWNER/SUBMISSION_REPO \
  --retained-input /private/provider-readbacks.json \
  --out /private/replayed-policy-context.json
```

The input is JSON with an `observed_at` timestamp and a `records` list. Each
record contains the exact requested `url`, `params` object, observed `status`,
numeric `http_status` when available, optional `headers`, and decoded provider
`payload`. Successful Contents payloads include `path`, `encoding` (`base64` or
`utf-8`), `content`, and optional native `sha`. A connector that supplies content
without a numeric HTTP code uses `status: "READ"` and `http_status: null`; replay
does not invent HTTP 200 or refresh the original observation time.

Both modes report `read_operations`. Live mode reports attempted
`provider_requests`; retained mode reports zero provider requests and the number
of `retained_readbacks_used`. A caller processing several bounties for one target
can call `collect_submission_policy_context(submission_repo, session=session,
token=token)` once and reuse the returned observation within that batch. The
caller owns its session and freshness policy. Source text belongs in private
working evidence, not a public PR, ordinary log or capture-v1 payload.
