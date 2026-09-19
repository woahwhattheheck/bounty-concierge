# GrantFox source baseline — RemitFlow/RemitFlow-Backend #134

Operation: `GFOX3-20260919-remitflow-remitflow-backend-134/R-readiness-recovery-map`  
Worker: ZZ-Sol-Rook · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `b0a004ab8aa00b1e6f262aa84ea2b0ec0a622e1b`

## Canonical issue and authority

- GitHub: https://github.com/RemitFlow/RemitFlow-Backend/issues/134
- GrantFox: https://contribute.grantfox.xyz/org/RemitFlow/repo/RemitFlow-Backend/issue/134
- Provider state at census: Unassigned, zero comments, live application route, `MAYBE REWARDED` / Third Campaign.
- No fixed reward, award, payment, or assignment is established by this packet.
- Upstream connector access at census was pull=true / push=false.
- This is a pre-assignment source audit only; no upstream source or provider state was mutated.

## Current-main failure mode

Pinned health blobs:

- `src/routes/healthRoutes.js` — `b60f184c3035a6d15e274ade6537ccefe59621d7`
- `src/controllers/healthController.js` — `0e41aa5f9de1c5cf2369bedf7d2d38e2865d630f`

The service already has separate `/api/health/live` and `/api/health/ready` endpoints. Liveness reports process responsiveness. Readiness, however, unconditionally returns:

`status: 'ready', checks: { store: 'ok' }`

The controller comment explicitly says the demo store is in-memory and readiness therefore mirrors liveness. This directly reproduces the issue's stated failure mode: readiness cannot become not-ready for a dependency outage.

## Scope mismatch: named dependencies do not exist on current main

The issue calls out database, payment-provider, and FX dependencies. Current main has no production dependency clients for those systems.

Pinned evidence:

- `package.json` — `a1bf7b6726b103f58de2e8cf6f2bab71fca6883b`
- `src/store/index.js` — `0024fac7dfbefe34054b9ad0f3ce4a8164828ee7`
- `src/config/rates.js` — `52f235a875affcbf31a15ac19108d2a12c0e6bad`
- `src/services/rateService.js` — `95c7112e9f88a003c5d34ade0cb02f05b8cd4da0`
- `src/services/stellarService.js` — `e77cea9f8ff55a38de68ee55f4a5563a7c748321`
- `src/config/index.js` — `41f9a7137046d1b8482d5261d727dcfa267ddc12`

`package.json` contains only cors, dotenv, express, morgan, and uuid. There is no pg/mysql/HTTP/provider SDK dependency. The store uses process-lifetime Maps plus an OrderedIndex and explicitly describes itself as an in-memory, dependency-free demo. FX rates are a static in-repository table. The Stellar service is a synchronous mock that fabricates identifiers rather than contacting a payment network.

`config.db.pool` exists, but code search found no database client or consumer of that configuration. Configuration alone is not a live dependency and should not be reported healthy.

Recursive source search also found no axios/fetch-based dependency client.

## Timeout and recovery semantics

Pinned middleware:

- `src/middleware/requestTimeout.js` — `ad9b53a3bd9faf5980d588b37a2944400e5a3836`
- `src/app.js` — `bd5b0652767bfb774ec992a331d8ec8826f27376`

The app-wide request timeout forwards a 503 after the configured response budget (15 seconds by default) if headers have not been sent. It does not cancel underlying work. A future readiness probe that calls real providers therefore needs its own shorter per-check deadline and cancellation/abort behavior rather than relying on the outer HTTP timer.

A recursive tree census found no health/readiness test file: only the controller and route contain health/readiness names.

## Assignment-safe next action

Before implementation, maintainers should choose one of two contracts:

1. identify the intended current branch/clients for the real DB, payment, and FX dependencies and bind readiness to those actual clients; or
2. explicitly confirm that #134 should first introduce an injectable dependency-check registry into the current demo architecture.

Adding fake network pings or claiming the static in-memory mocks represent production dependency health would make readiness misleading.

If the injectable registry is the intended scope after assignment:

- liveness performs zero dependency I/O;
- readiness evaluates only required serving dependencies;
- each check has an explicit deadline shorter than the request timeout and a cancellation strategy;
- one hanging check cannot hold the whole readiness request indefinitely;
- all required checks healthy => 200/ready; any required check failed/timed out => 503/not-ready;
- responses expose stable, allow-listed reason codes instead of raw exception strings, URLs, credentials, tokens, or provider payloads;
- failures are not latched, so a recovered dependency makes the next readiness probe healthy without restart;
- tests cover failure, timeout, recovery, HTTP status, parallel-check behavior, and redaction using injected fakes.

No live dependency was contacted and no provider application was submitted by this baseline.
