# Recorded checks

The text/JSON receipts were produced from this source on October 4, 2026.

- `model-checks.txt`: six local Node model/export tests.
- `adapter-checks.txt`: four Python adapter and loopback HTTP tests.
- `projection-benchmark.json`: 10,000-row synthetic projection benchmark; no DOM or provider latency.
- `browser-receipt.json`: desktop/mobile in-memory browser replay. Browser-to-localhost navigation was denied by administrator; no policy override was attempted. The real server was exercised separately by the Python HTTP test.
- `demo-snapshot.json` and `demo-schedules.ics`: exports actually generated through the synthetic browser interface. These contain no real market records or live provider responses.

`tests/browser_check.py` reproduces screenshots and a WebM walkthrough with Playwright. Browser media are intentionally excluded from the source tree. A separately supplied MP4 walkthrough uses synthetic data and is slowed for readability; it is not a latency benchmark.

No real Panta key was used. API replay, local HTTP checks and offline UI execution do not establish successful authenticated live integration or a contest submission.
