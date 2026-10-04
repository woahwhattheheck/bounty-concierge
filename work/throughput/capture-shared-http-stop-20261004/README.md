# Stop capture batches after shared HTTP failures

`collect_batch` previously continued after every HTTP 4xx except 401 and 429.
That treated a failure of GitHub search or a linked PR detail read as an
unavailable issue. A shortlist could repeatedly enter the same broken provider
path and consume its entire request budget without producing a capture.

The batch now continues after HTTP 404/410 only when the failed request is the
current canonical issue endpoint. Other HTTP failures stop with the existing
`HTTP_ERROR` status. The incomplete current issue and all unattempted issues stay
in `remaining.json`. Actual endpoint identity is retained only in the private
transport object; summaries do not gain URLs or provider response text. Redirect
requests retain their own endpoint identity. This matches the existing audit
batch's narrow unavailable-issue handling.

## Observed request counts

Ran `collect_batch` through the actual preflight, audit, capture and secure-output
code with Requests 2.34.2 / Python 3.12.14. A transport adapter redirected only the
wire destination to a local HTTP/1.1 server; production functions, response
objects, redirect handling, error classification, output files and request counts
were not replaced. Every counted request also reached the local server. No GitHub
or other external provider request was made by this experiment.

| Scenario | Issues | Before GETs | After GETs | Result |
| --- | ---: | ---: | ---: | --- |
| Search 403, positive remaining quota | 25 | 75 | 3 | Stop after first failure; all 25 remain |
| Shared linked PR detail 404 | 2 | 8 | 4 | Stop after first failure; both remain |
| Canonical issue 404, then valid closed issue | 2 | 4 | 4 | Second issue captured; first remains |
| Canonical issue 410, then valid closed issue | 2 | 4 | 4 | Second issue captured; first remains |
| 429 with Retry-After and reset time | 2 | 1 | 1 | Existing cooldown metadata preserved |
| Canonical issue redirects to search 404 | 2 | 4 | 2 | Redirect destination treated as shared failure |

The 25-issue fixture uses 72 fewer requests (96%). This is a measured failure-path
request reduction, not a fleet throughput or successful-provider latency claim.
The valid control issue is closed, so its completed capture does not authorize
new work. No retries, schedules, freshness changes or payment decisions were
introduced.

## Reproduce

From the repository root, run the six focused scenarios:

```bash
python work/throughput/capture-shared-http-stop-20261004/reproduce.py
```

To repeat the comparison with the pre-change production file:

```bash
git show 089af58810d54ad4ca5689eae17df76969e26ccb > /tmp/capture-batch-before.py
python work/throughput/capture-shared-http-stop-20261004/reproduce.py \
  --baseline /tmp/capture-batch-before.py
```

The script creates only temporary capture directories and a loopback server. It
uses a fixture-only token and disables environment proxy inheritance. The raw
recorded output is in `results.json` beside this guide.

Executed batch blobs: `089af58810d54ad4ca5689eae17df76969e26ccb` before,
`13fa7e3349192fe880b0094e8b173573ea42d0db` after. The supporting current source
blobs were preflight `cc78fff3f030c8d222030a4355201978e7b5cf2d`, audit
`09acb74e852306c2b107d197d5c29654bc73f26a`, and secure output
`a0e8953fc8cad9ab62c0b2708325875c4c5e0573`; the remaining import closure was reused
from a retained source copy. No dependency installation, full suite or hosted
workflow was needed.
