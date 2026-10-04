# Redirected issue failures: batch progress

## Delivered behavior

An issue GET that follows only GitHub issue-to-issue redirects can now retain issue-local 404/410 handling. The batch preserves the initiating URL and requires every attempted hop to match a GitHub issue endpoint. A hop to another host or a non-issue endpoint permanently removes that exception for the logical request. The failed candidate remains in the output; unrelated candidates may continue.

Shared search/PR failures, unrelated redirects, transport failures, quota stops and request budgets retain their existing behavior. There are no extra retries, provider writes, freshness changes, settings, dependencies or background tasks. Existing retry-shortlist ordering and redirect accounting are unchanged.

## Execution scope

Executed the complete production `concierge/bounty_capture_batch.py` before and after the patch with Python 3.13.5 and Requests 2.32.5. Real `requests.Session` redirect preparation, response hooks, response cleanup, batch classification, counters and disk outputs ran. An offline adapter supplied explicit HTTP responses; preflight/qualification and filesystem-policy imports were controlled collaborators. Every unexpected request failed rather than reaching the network.

This is a bounded batch/transport replay, **not** full qualification, a live marketplace scan, a provider latency benchmark, or the repository suite. The synthetic preflight makes one issue GET per ordinary candidate; its counts must not be advertised as full production qualification costs. Zero external HTTP calls were made.

The shortlist contained 25 candidates. The first was the failing case; the other 24 returned ordinary successful fixture responses.

| Scenario | Before: captures / GET attempts / stop | After: captures / GET attempts / stop |
| --- | --- | --- |
| Issue redirects to numeric repository issue URL, then 404 | 0 / 2 / HTTP_ERROR | 24 / 26 / ITEM_ERRORS |
| Issue redirects to renamed repository issue URL; response hook raises 410 | 0 / 2 / HTTP_ERROR | 24 / 26 / ITEM_ERRORS |
| Shared search redirects to issue URL, then 404 | 0 / 3 / HTTP_ERROR | unchanged |
| Issue redirects off-host, then 404 | 0 / 2 / HTTP_ERROR | unchanged |
| Issue redirects off-host and back to issue URL, then 404 | 0 / 3 / HTTP_ERROR | unchanged |
| Issue redirects, then 429 | 0 / 2 / RATE_LIMITED | unchanged |
| Direct issue 404 | 24 / 25 / ITEM_ERRORS | unchanged |
| Request budget of one, with a pending redirect | 0 / 1 / REQUEST_LIMIT | unchanged |

All eight scenarios passed their expected outcomes. Every recorded response was closed. All 25 candidates remained accounted for as captured or remaining. The 429 retained Retry-After 17 and reset 1791117000. Successful supply bytes matched between both repaired redirect cases and the direct-404 control; all unchanged controls matched before/after, including remaining ordering. Endpoint-tracking fields did not appear in the output item records.

The gain is completing 24 otherwise blocked candidates within the existing budget, **not fewer total requests**: the additional GET attempts are the newly reached candidates, not retries.

## Source identity and use

Before: commit `58209a44ad7060043fdd02dd2af2fb048d5e4eb4`, module blob `59ad10ac9bd6a0f3587277d527df76620d50aa6e`.

After: source commit `eb4e42eb0ac0b415365c797079e615644cdeae85`, module blob `9b1b525504944e59add8fc66a42fc1f32cea9f3e`.

Both complete source blobs were checked against the executed bytes. The ordinary `python -m concierge.bounty_capture_batch` command adopts the behavior from this source without a new flag. No permanent test suite or CI workflow was introduced.
