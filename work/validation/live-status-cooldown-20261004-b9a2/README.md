# Live-status cooldown visibility — 2026-10-04

The complete production module previously returned `GITHUB_HTTP_429` while
discarding GitHub's wait evidence. The change adds the optional
`live.provider_cooldown` object to its existing receipt.

Source baseline: `b855a699d70a33c6c9efdd9bcafc330ebeabd3ac`.
Unchanged-before blob: `57e13a9fbd2789c87abca07474cef24ad8365582`.

## Observed results

Python 3.12.14, Requests 2.34.2. Fourteen controlled cases passed.
Every before/after invocation made exactly one call at the private HTTP seam;
the replay made zero live network requests.

| Provider response | Before | After |
|---|---|---|
| 429, Retry-After 90 | Delay lost | 90 seconds and an absolute UTC lower bound |
| 429, HTTP date | Delay lost | Wait rounded upward |
| 403, exhausted primary quota and shorter Retry-After | Delay lost | Later reset retained |
| 403, exhausted primary quota and longer Retry-After | Delay lost | Longer Retry-After retained |
| Permission-only 403 | No quota annotation | Unchanged |
| 429, missing/malformed/oversized header | Delay unknown | Quota visible, delay explicitly null |
| 429, valid date-overflowing seconds | Delay lost | Full seconds retained; date null |
| Open issue, 404, 410, cross-host redirect, transport failure | Existing result | Unchanged |

After removing the new optional object and resealing, all fourteen complete
results match the baseline byte-for-byte under the fixed clock. Both versions'
receipts verify, and retained-receipt qualification stays false.

## Reproduce

From a checkout containing this change and its baseline:

```sh
replay_dir=$(mktemp -d)
git show b855a699d70a33c6c9efdd9bcafc330ebeabd3ac:concierge/bounty_live_status.py > "$replay_dir/before.py"
cp concierge/bounty_live_status.py "$replay_dir/after.py"
cp work/validation/live-status-cooldown-20261004-b9a2/replay.py "$replay_dir/replay.py"
PYTHONDONTWRITEBYTECODE=1 python "$replay_dir/replay.py"
```

The fixture executes each entire source file. It replaces the private HTTP
transport with real Requests Response objects and fixes the module clock.
Package bootstrap is excluded: only an empty-token config module is supplied.
No positive live-provider/whole-package/CLI integration result is asserted.
There are no dependency installations, provider retries, sleeps, or fleet-speed
measurements. This records visibility at the result boundary; existing budget
consumers must use that evidence to defer subsequent requests.

Executed SHA256:
- before: `8078f1ed9f081023347dad9bcf131a664c3be660f7e73a491c90b1c92409b2b0`
- after: `2b0c76e9e4be066b5145cc8f08bc341870224067647711aacb3683d315901234`

The adjacent `results.json` retains all cases and counts.
