# BountyHub HTTP response-hook handling

## Source and execution

The production edit is confined to `concierge/bountyhub_catalog.py::_get`.

- Baseline commit: `2e4d21e346a7e90084e5e41410c66176791b2eee`.
- Baseline source blob: `5e5efa110379357df1fd0154ee9a20f004fac262`.
- Fixed source commit: `51ff6b99ec62e37006330a2aaeaecb45df10e7c9`.
- Fixed source blob: `269a9e9a58c3f0e8081cd83f50049933a9fbac3d`.
- Executed on 4 October 2026 with Python 3.13.5 and Requests 2.32.5.

Run from the repository root with the existing Requests dependency installed:

```sh
python work/validation/bountyhub-http-hooks-20261004/replay.py concierge/bountyhub_catalog.py
```

To reproduce the baseline, extract the pinned baseline source to a temporary file and pass that file as the final argument. The replay loads the actual complete module, substitutes only its API origin with a loopback HTTP server, and uses an actual Requests Session response hook that calls `raise_for_status`. Synthetic listings and pledge records never leave loopback. The separate response-less transport control injects an HTTPError directly.

## Observed results

| Case | Baseline | Fixed |
| --- | --- | --- |
| Catalog 429 with Retry-After 7 | Status and delay lost, not marked throttled, response not closed | HTTP_ERROR / 429, delay 7, throttled, response closed |
| Catalog 403 with Retry-After 11 | Status and delay lost, response not closed | HTTP_ERROR / 403, delay 11, throttled, response closed |
| Catalog permission 403 | Status lost, response not closed | HTTP_ERROR / 403, no fabricated retry delay or throttling, response closed |
| Detail 404 followed by valid detail | 2 GETs; later row NOT_ATTEMPTED | 3 GETs; later row COMPLETE |
| Detail 410 followed by valid detail | 2 GETs; later row NOT_ATTEMPTED | 3 GETs; later row COMPLETE |
| Detail 429 | Status and delay lost | 429 and delay 7 retained; later row still deferred; 2 GETs |
| Detail 500 | Status lost, response not closed | 500 retained, response closed; later row still deferred; 2 GETs |
| Successful empty catalog | Pass | Pass |
| HTTPError without a response | Pass | Pass |

Baseline: **2/9 pass, exit 1**. Fixed: **9/9 pass, exit 0**. Each fixed response was closed exactly once by the collector before replay cleanup. The replay also checks physical request counts, unchanged partial-report semantics, and the success control. `git diff --check` passed.

## Scope

No automatic retries, sleeps, new limiter, provider writes, eligibility change, payment claim, catalog sweep, dependency installation, or hosted CI run was added. These are local behavioral observations, not live BountyHub rate-limit measurements or a fleet-wide speedup benchmark. A caller-provided response hook is required to reach the repaired path; ordinary returned-response handling is unchanged. The existing HTTP-date Retry-After parser is reused without modification; the replay uses numeric delay headers.
