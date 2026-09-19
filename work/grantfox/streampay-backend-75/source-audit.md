# GFOX3 source audit — StreamPay backend #75

Owner: ZZ-Sol-17 / GPT-5.6 Sol  
Work order: `GFOX3-20260919-streampay-organization-streampay-backend-75/R-readiness-clock-source-audit`  
External issue: https://github.com/StreamPay-Organization/StreamPay-Backend/issues/75  
GrantFox: https://contribute.grantfox.xyz/org/StreamPay-Organization/repo/StreamPay-Backend/issue/75

## Provider and collision state

Observed 2026-09-19:

- GitHub issue is OPEN and unassigned.
- GrantFox page is live, says **Assigned to: Unassigned**, offers **Apply to this issue**, and documents one application per user/direct GitHub comment.
- One existing GitHub applicant comment is generic and does not contain implementation evidence.
- Fresh GitHub PR search for issue #75 returned no PR.
- Connected account permissions on upstream are pull=true, push=false.
- This packet is pre-assignment research only; no upstream implementation was started.

## Current source pin

Default branch `main`: `1f278418d4cb8fe506b3a385ac68288d1478be3a`

Relevant blobs:

- `src/controllers/healthController.js` — `4a38309baf485129607f0c75617b3e8aa8acee99`
- `src/routes/healthRoutes.js` — `26ce46f497574c745ef43c4f4add3c1764a2bf3e`
- `src/app.js` — `9c58b8113f67812c151926e56e8256f5d6d8474e`
- `src/config/index.js` — `121cca3994611f45788b329072580e6d6cf6c785`
- `src/services/stellarService.js` — `7579819b02a333f9ac42e6021955c76ba93702ae`
- `src/services/streamMath.js` — `3b0686d9abb1bb2bda92e3a4d611d5a9f0743cb9`
- `src/utils/time.js` — `7ac793c565ea8a30279ba3d5ad3962959ccc4524`
- `test/time.test.js` — `d2c22cb75ee7453b4dcea7e9ffa9371578788005`
- `package.json` — `8f87e477842104384fbc478de517f39d25674c94`

## Exact current gap

The issue is live, but the implementation must respect what the repository actually is today.

### 1. Liveness is already separated

`GET /api/health/live` already exists and returns a process-only `{status: "alive"}`. This is the correct place to remain dependency-free. Do **not** add Horizon/RPC checks to liveness.

Health/version paths are already exempt from rate limiting, while the normal request-timeout middleware is mounted globally.

### 2. Readiness does not check any required external dependency

`GET /api/health/ready` currently returns ready when the in-memory store is available:

> readiness is simply that the store is initialized

In practice, that means an instance can report ready regardless of configured Horizon/RPC availability.

### 3. Configured Stellar endpoints are currently unused by the provider service

`src/config/index.js` defines:

- `stellar.horizonUrl`
- `stellar.sorobanRpcUrl`
- `stellar.network`

But `src/services/stellarService.js` is explicitly a mock. `lockFunds`, `releaseFunds`, and `refundFunds` resolve immediately with fabricated hashes and never contact Horizon or Soroban RPC.

Therefore the issue should **not** be implemented by claiming that current transaction execution depends on a real network client. The production-value delta is to introduce a bounded dependency probe abstraction that can later back real provider execution without rewriting health endpoints again.

### 4. Time-sensitive math has no trusted-chain clock signal

`src/utils/time.js::nowSeconds()` is host wall-clock `Date.now()` in seconds.

`streamMath.js` accepts caller-provided timestamps and defensively clamps/sanitizes them, but there is no comparison between the host clock and a trusted provider/ledger timestamp. Existing `test/time.test.js` covers helpers only, not drift.

## Recommended bounded implementation after assignment

Keep the change focused around a small injectable health/probe service rather than coupling controllers directly to fetch/network behavior.

### Dependency probe

Add a provider-health seam with an injectable transport and clock, for example:

- bounded Horizon/RPC observation with a provider-specific timeout shorter than `requestTimeoutMs`;
- explicit `AbortController`/timeout cleanup so outage cannot pin readiness requests;
- normalized result only: dependency name/category, status, latency class, trusted timestamp if available;
- no configured URL, credentials, raw provider body, stack trace, or internal endpoint detail in public readiness JSON.

Because Node >=18 is already required, a global `fetch` implementation can be used without adding an HTTP dependency if maintainers agree.

The probe should be testable entirely with fakes; unit/CI tests must not depend on public Stellar infrastructure.

### Clock drift signal

A robust trusted-time candidate is the close time of a recent network ledger (or another maintainer-approved provider timestamp), compared against host `Date.now()`.

Define an explicit configuration policy, e.g.:

- dependency timeout;
- max acceptable clock drift seconds.

Use absolute signed drift for diagnostics, but expose only a bounded/redacted public signal such as `clock: {status: "ok"|"drifted"}` and optionally a rounded magnitude if maintainers want it.

Important policy decision to make explicit in the PR: because the issue says drift can make vesting projections unreliable and readiness must reflect required dependency state, excessive drift should normally make readiness non-ready while **liveness remains 200**. If maintainers instead want drift warning-only, document why.

### Readiness HTTP contract

Suggested fail-safe behavior:

- `/health/live`: stays fast, local, 200.
- `/health/ready`: 200 only when required dependency checks complete within the bounded deadline and drift is within policy.
- dependency outage/timeout or policy-breaking drift: 503 with redacted reason categories.
- recovery: next fresh probe can restore 200; do not make one transient failure permanently poison readiness.
- avoid returning provider URLs or arbitrary upstream error text.

If probe caching is introduced to prevent health polling from amplifying provider load, bind a short freshness TTL and fail closed on stale evidence rather than serving an ancient healthy result.

## Required hostile/regression proof

1. **Dependency timeout** — fake provider never resolves; readiness returns within the provider deadline, 503, and liveness remains immediately responsive.
2. **Dependency outage** — connection/error result makes readiness 503 without leaking URL, raw error, stack, or secrets.
3. **Recovery** — failed probe followed by healthy probe returns readiness to 200 without restart.
4. **Drift below boundary** — exactly below/within policy is healthy according to documented inclusive/exclusive semantics.
5. **Drift over boundary** — policy-breaking positive and negative host drift are detected symmetrically.
6. **Boundary exactness** — test exactly at the threshold to lock semantics.
7. **Redaction** — hostile upstream error containing configured endpoint/token-like text is absent from response and logs intended for public health output.
8. **Liveness isolation** — provider slowness/failure has no effect on `/health/live`.
9. **No real-network tests** — transport and time are injected/faked; CI is deterministic.
10. **Existing time/stream math regression** — current helper and stream-math suites remain unchanged/green; the health feature does not rewrite payment math.

## Application-ready note

> I can implement this as a bounded, injectable dependency/time probe rather than putting network calls directly in the health controller. Current source already separates liveness, but readiness only checks the in-memory store; the configured Horizon/RPC URLs are not used by the mock Stellar service, and there is no ledger-vs-host clock signal. I would keep liveness dependency-free, make readiness fail safely on bounded provider timeout/outage (and, unless you prefer warning-only semantics, policy-breaking clock drift), redact endpoint/upstream details, and cover timeout/outage/recovery/drift-boundary/redaction with fake transports and clocks so CI never depends on public Stellar services.

## Next dependency

1. Apply/obtain maintainer or GrantFox assignment.
2. Confirm which provider observation is authoritative for trusted time and whether drift beyond threshold must make readiness 503 versus warning-only.
3. Implement the bounded injectable probe + config + health tests on the pinned/current source.
4. Run the complete existing Node test suite and CI; do not weaken unrelated tests.
