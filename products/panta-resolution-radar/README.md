# Resolution Radar

**Powered by Panta.** A read-only resolution desk for Panta prediction-market schedules.

Resolution Radar turns catalog data into an ordered view of schedules that have passed, markets scheduled to resolve within 24 hours, and records missing a resolution time. Search and filter the loaded catalog, keep a browser-local watchlist, inspect a market on demand, and export a source-stamped JSON snapshot or tentative UTC calendar events. It does not trade, sign transactions, create markets, or infer winning outcomes.

## Run

Python 3.10 or newer is sufficient for the application; no package installation or JavaScript build is needed. Python 3.13.5 and Chromium were used for the recorded checks.

```bash
cd products/panta-resolution-radar
python3 server.py
```

Open `http://127.0.0.1:8787`. The default is **DEMO**, with eight explicitly synthetic markets. Addresses, volumes and detail prices in this mode are not real market evidence.

For live mode, configure an existing authorized Panta API key in the server environment, then run:

```bash
python3 server.py --live
```

The variable is `PANTA_API_KEY`. The key remains on the server; do not put it in browser storage, source control, command-line arguments or exported snapshots. Without it, `--live` exits and the interface disables the live selection. An upstream failure never substitutes demo data.

The default bind address is loopback. This is a local application, not a public authenticated hosting service. Do not expose a credential-bearing instance publicly without an appropriate authenticated reverse proxy. No deployment, account registration or terms acceptance is performed by these commands.

## What is implemented

- Catalog pagination follows Panta's opaque `nextCursor`, with 50 rows per explicit read. Duplicate market IDs are merged. Repeated cursors stop paging rather than create a request loop. Filters and counts describe **loaded rows**, not the entire chain.
- `resolutionTime` is handled as Unix seconds, separately from `endTime`. Unknown phases and missing times remain unknown. Resolved and cancelled records are excluded from upcoming calendars. A passed schedule does not establish a failed settlement.
- Market detail is read on demand. Catalog list prices are not treated as live spot prices. Missing prices and volumes display “Unavailable,” not zero. Decimal-string volumes retain precision.
- Demo and live state are separated. Changing mode clears old rows, cursor and detail selection. Watchlists store IDs only and are separated by data mode. Stale detail responses cannot replace a newer selection.
- All live reads use the fixed documented Panta API origin. One request is in flight per server process; a bounded 60-second cache coalesces repeated reads and preserves the original response timestamp. A provider `429` establishes a shared cooldown across catalog and detail routes. There is no automated polling, retry loop or account switching. Independent server processes do not share this cache or cooldown.
- JSON exports preserve source mode, page timestamps, pagination coverage and active filters. Calendar exports contain only filtered, loaded, future schedules; events are tentative and demo event titles are prefixed `[DEMO]`.

## API integration

The adapter calls only `GET /markets/` and `GET /markets/{marketId}/` at `https://live-api.panta.market/api/v1`. It uses `X-Api-Key`; no wallet or trading endpoint is included. Provider rows are projected onto the documented catalog fields rather than copying unrelated account metadata.

The implementation follows the official [list contract](https://github.com/Kaito-HQ/panta-api-pub/blob/main/api-reference/markets/list.mdx), [detail contract](https://github.com/Kaito-HQ/panta-api-pub/blob/main/api-reference/markets/get.mdx) and [catalog notes](https://github.com/Kaito-HQ/panta-api-pub/blob/main/api-reference/markets/catalog.mdx), read October 4, 2026. These sources specify Unix seconds, opaque cursors, a maximum page size of 50, and null list prices with conditional spot prices on detail. The official [playground](https://github.com/Kaito-HQ/panta-api-playground) documents authentication and the “Powered by Panta” product attribution displayed by this application.

## Focused checks and performance

```bash
node --test tests/model.test.mjs
python3 -m pytest -q tests/test_server.py
node tests/benchmark.mjs
```

Only `pytest` is needed for the four adapter tests; Node 22 was used for the six model/export tests. Runtime application code has no third-party dependencies.

Recorded on October 4, 2026: **6/6 Node tests and 4/4 Python tests passed**. Eight concurrent identical catalog reads produced one replayed upstream GET; another GET occurred only after cache expiry. Auth failures and provider cooldowns did not fall back to demo data.

The local projection benchmark performs filtering, full schedule ordering and summary calculation on **10,000 synthetic rows**. Across 30 samples after five warmups, median was **12.109 ms** and p95 **16.154 ms**, on Node v22.16.0 / Linux / Intel Xeon Platinum 8573C. It measures no provider traffic, DOM rendering, production latency or fleet throughput. This is not a before-and-after speedup claim.

For the browser walkthrough, install Playwright and its Chromium/FFmpeg dependencies, and supply a Chromium executable at `/usr/bin/chromium` (or set `RADAR_CHROMIUM` to its path):

```bash
python3 tests/browser_check.py
```

A constrained environment can render the same source with a **clearly separated in-memory synthetic API replay**:

```bash
python3 tests/browser_check.py --offline
```

The recorded desktop 1440×960 and mobile 390×844 check used this offline mode because the harness administrator blocked browser navigation to localhost. No browser policy was changed. The separate Python integration test exercised the actual loopback HTTP server. Browser checks covered filtering, watchlists, detail, exports, search and mobile overflow with no page-script errors. This is **not** a live Panta end-to-end pass. Receipts and a reproducible video driver are included; the demo video depicts synthetic data.

## Demo / pitch

The problem is operational context: a catalog lists questions, but a resolution desk must distinguish the market end from the scheduled resolution, a missing field from a zero, and an old snapshot from a new observation. Resolution Radar makes those distinctions visible and portable instead of adding another trading screen.

Walkthrough: inspect the “Schedule passed” view, open one market's source and timings, star it, export a timestamped snapshot, then switch to “Next 24 hours” and export tentative calendar events. Every displayed record carries its mode and read timestamp. Panta's catalog and detail APIs are the meaningful data path in live mode.

**Delivery boundary:** the prototype and replay/loopback checks are complete. An authorized live key, a real live capture, and the existing main Crypto World's Fair / Earn entry binding still need to be supplied before representing a live deployment or formal contest submission. No registration, submission, award, payout or live market response is claimed by this source delivery.
