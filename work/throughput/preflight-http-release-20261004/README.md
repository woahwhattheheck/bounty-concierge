# Preflight HTTP response release

The preflight `_get_json` helper retained unread streamed HTTP error responses in the exception cause. A caller-owned one-slot blocking connection pool had zero available slots after either an ordinary streamed 403 or a response-hook HTTPError. Closing the caller session was not a suitable fix because it owns subsequent work.

The helper now retains the error body needed by `_http_error_result`, then releases the response in `finally`. Failed body reads or cleanup do not replace the original failure. Captured responses without a close method remain supported. Sessions, request budgets, retries, issue freshness, claim rules and payment state are unchanged.

## Observed results

`results.json` is emitted by `replay.py` using the production module and real loopback HTTP. The exact previous helper is loaded from a blob-pinned source file into the same dependency namespace for comparison.

- Streamed 403 and hook-raised 403: available pool slots change from 0 to 1. The error response is closed, its body remains readable, and the next request succeeds on the same TCP connection.
- Success and invalid JSON retain their previous payload/error, Link metadata, request count and connection reuse behavior.
- Frozen capture responses without `close` still work. Failed draining and failed cleanup preserve the original HTTPError cause.

No live GitHub traffic, provider throughput benchmark, account operation, source-claim change or bounty payment is claimed. A drained error may require reading its remaining body; failed drains still close the response. This does not increase provider quota or replace cooldown enforcement.

## Reproduce

```sh
git show f2fd8ea03c950d2b5e5425caa6161b6fbdc20142:concierge/bounty_preflight.py > /tmp/preflight-before.py
python work/throughput/preflight-http-release-20261004/replay.py --baseline-source /tmp/preflight-before.py
```

Baseline blob: `cc78fff3f030c8d222030a4355201978e7b5cf2d`.
Patched blob: `820f0e432d923442882bcbd05465212b1524da09`.
The original contributor history and all unrelated preflight code remain intact.
