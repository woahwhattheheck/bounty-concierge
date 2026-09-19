# RemitFlow #129 — source-pinned FX cache / stale-data baseline

**Work order:** `GFOX3-20260919-remitflow-remitflow-backend-129`  
**Upstream:** `RemitFlow/RemitFlow-Backend#129`  
**Audited main:** `b0a004ab8aa00b1e6f262aa84ea2b0ec0a622e1b`  
**Audit seat:** ZZ-Sol-Cormorant-47 / GPT-5.6 Sol  
**Disposition:** pre-assignment source/readiness packet only. This file is not provider assignment, implementation authority, reward proof, or a claim that an upstream PR exists.

## Current-main source truth

The issue asks for bounded FX cache TTL, provider fallback, freshness metadata, quote versioning, explicit stale/error policy, stampede prevention, and transfer↔quote binding. Current main has almost none of that provider-data model yet:

- `src/services/rateService.js` (`95c7112e9f88a003c5d34ade0cb02f05b8cd4da0`) reads one in-process static `RATES_TO_USD` table. There is no provider interface, provider identity, fallback chain, async fetch, data cache, refresh lock/single-flight, or provider error state.
- `src/services/quoteService.js` (`3cba17a0cbad99a0128c6130383702f949baa0d6`) calls `rateService.getRate()` and returns `{from,to,sendAmount,fee,amountAfterFee,rate,receiveAmount}`. There is no quote id/version, provider id, `asOf`, `expiresAt`, freshness/stale flag, or policy decision.
- `src/services/transferService.js` (`1517f6254bf18cf1be4e7d52cf369e637d8ffb0f`) recomputes a quote inside transfer creation and persists only the numeric `rate` plus amounts. A client cannot bind transfer creation to a previously observed quote identity because none exists.
- Existing idempotency is useful but separate: replay short-circuits before quote recomputation, so one idempotency key preserves the original transfer/rate. It does **not** prove a first-time transfer consumed a specific quote returned earlier by `GET /api/quote`.
- `src/store/index.js` (`0024fac7dfbefe34054b9ad0f3ce4a8164828ee7`) is process-local/in-memory. Any FX cache added at the same layer has single-process lifetime unless scope explicitly changes.
- `src/config/index.js` (`41f9a7137046d1b8482d5261d727dcfa267ddc12`) has `CACHE_RATES_MAX_AGE_SECONDS`, but that currently controls HTTP response cache headers only.
- Commit `55b11323687f6b329dc1eec0b1041bce0434a58f` added rate **HTTP Cache-Control** behavior. Do not mistake CDN/client response caching for the provider-data cache required by #129.
- CI is a small Node 22 rail: `.github/workflows/ci.yml` blob `bbe235fe408cfb2e297e61d702faf3e1f3e593e4` runs `npm ci` + `npm test`.

Issue state at audit: OPEN, GitHub-unassigned, GrantFox OSS + Maybe Rewarded + Third Campaign, three generic applicant comments, and no open PR found by issue-number search. Upstream connector permissions are pull-only, so implementation is assignment-gated.

## Assignment-time design contract

Keep the change focused; do not turn a demo backend into a distributed-rate platform.

1. **Provider abstraction / deterministic chain.** Put raw FX retrieval behind a small injected provider contract. Define an ordered provider list and deterministic fallback: provider A success wins; retryable provider A failure can advance to B; invalid/malformed data is a hard provider failure, never silently current.
2. **Data-cache record, not HTTP cache.** Cache by normalized pair (or normalized base snapshot if the implementation uses one) with at least `rate`, `provider`, `version`, `fetchedAt/asOf`, and `expiresAt`. TTL must be bounded/configured and validated.
3. **Single-flight refresh.** At most one refresh per cache key while expired/missing; concurrent callers join the same in-flight refresh. Provider call count is an assertion, not just latency behavior.
4. **Explicit freshness policy.** Distinguish at least current, stale-visible-but-not-usable, and unavailable/error. A stale quote may be returned only where the API contract explicitly exposes its staleness; transfer creation must refuse a quote beyond the allowed freshness window.
5. **Stable quote identity.** Quote output needs a stable identity/version bound to the exact rate snapshot and conversion inputs. Identity must not be a caller-provided opaque string trusted without server-side lookup/verification.
6. **Transfer binding.** A transfer created from a prior quote must bind the persisted transfer to that exact quote/version/provider/as-of record, revalidate freshness at the money-moving boundary, and reject expired/unknown/mismatched quote ids before `stellarService.submitPayment()`.
7. **Preserve idempotency semantics.** Existing `Idempotency-Key` replay must continue to replay the original terminal transfer without refreshing/repricing it. Conflicting retries still fail before provider/settlement side effects.
8. **No fake production network requirement.** Current Stellar and rate sources are mocks. Tests should use injected deterministic providers/fake clocks; do not introduce live network dependence just to satisfy “provider” language.

## Required hostile / regression panel

- cache hit inside TTL => zero additional provider calls;
- exact expiry boundary and just-after-expiry behavior;
- primary failure => deterministic fallback, with provider identity/freshness recorded;
- all providers fail + fresh cache exists => declared policy, no hidden fallback;
- all providers fail + only expired cache exists => stale is marked and transfer use is refused;
- N concurrent misses/expiries => one refresh call per key (single-flight);
- separate currency pairs do not cross-contaminate cache or single-flight state;
- malformed/NaN/nonpositive/out-of-range provider rate is never promoted current;
- quote id/version changes when underlying provider snapshot changes and remains stable for identical cached snapshot;
- unknown/tampered quote id refused before settlement;
- quote expires between preview and transfer => refused before `submitPayment`;
- transfer payload pair/amount incompatible with bound quote => refused;
- accepted transfer persists quote id/version/provider/as-of alongside the numeric rate/amounts;
- idempotent replay after rates move returns the original transfer and causes zero quote refresh / settlement calls;
- provider recovery after a failed refresh becomes current without serving the failed generation;
- test fake clock proves TTL logic without sleeps.

## Compatibility questions to resolve at assignment

- Whether `GET /api/quote` remains backward compatible by adding metadata fields versus requiring a new explicit quote-consumption parameter on `POST /api/transfers`.
- Whether stale quotes are exposed with 200 + explicit freshness metadata or a non-2xx response. Whichever contract is chosen, stale data must never look current.
- Whether cache lifetime is intentionally process-local (consistent with this demo's in-memory transfer store) or needs a persistence abstraction. Do not accidentally promise cross-instance/restart guarantees the repo cannot provide.

## Validation

Run the existing full `npm test` suite plus focused unit/integration tests for provider failure, TTL/expiry, fallback, stampede, stale-use refusal, quote identity, transfer binding, and idempotent replay. CI currently requires only Node 22 / `npm ci` / `npm test`; do not weaken unrelated tests.
