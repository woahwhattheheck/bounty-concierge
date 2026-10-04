# Release batch response-hook failures

`_BatchSession.get` now closes the response attached to a hook-raised
`requests.HTTPError` after recording its HTTP and rate-limit metadata. Requests
runs response hooks before consuming a non-streaming response body, so the
previous exception path left the failed response holding an unread connection.
The existing batch continues after an issue-specific 404, making immediate
response cleanup necessary even when the caller owns the Session.

The change preserves the original HTTP error if closing the response raises.
Normally returned responses and caller-owned Sessions retain their ownership.

## Focused replay

Run from a checkout with the project dependencies available:

```bash
python -B work/throughput/batch-http-release-20261004/replay.py --source-root .
```

The replay imports the entire production batch module and its normal package
dependencies. It uses real Requests against a local HTTP/1.1 server. The 404 and
429 cases execute `collect_batch`; two direct transport controls cover returned
stream ownership and a cleanup exception. Fixture traffic never leaves loopback.
Its temporary captures and network resources are cleaned up after each run.

| Case | Before | After |
| --- | --- | --- |
| 404 followed by a valid closed issue | 1 capture, 4 GETs; failed response left open | Same capture and GET counts; failed response closed once and released |
| 429 with Retry-After 17 | 1 GET, RATE_LIMITED; failed response left open | Same stop and retry/reset metadata; failed response closed once and released |
| Successful streamed response | Returned unchanged and open | Returned unchanged and open |
| Response cleanup raises | Original HTTPError; cleanup never called | Same original HTTPError after one cleanup call |

Both runs used Requests 2.34.2 and caller-owned Sessions; the production code
closed none of those Sessions. Total fixture GET attempts were 14 across both
runs. `results.json` retains the raw observations and source/dependency hashes.

The baseline batch file is Git blob
`40f700e150a97cbc631a0fe53c67de9876dea9dc`, read at commit
`b855a699d70a33c6c9efdd9bcafc330ebeabd3ac`. The repaired batch file is
`7cd1ab630e4a0ee20bb662c88872ff36458e9678`. Both isolated runs used identical
dependency copies; their provenance is recorded in the results. This measures
response release and preserved request behavior, not fleet throughput, live
GitHub latency, or payout performance.
