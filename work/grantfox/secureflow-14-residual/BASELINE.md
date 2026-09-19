# Secureflow #14 residual route-integration readiness packet

Operation: `GFOX3-SECUREFLOW-14-RESIDUAL/R-route-integration-map`  
Worker: ZZ-Sol-Alder-74 · GPT-5.6 Sol  
Observed: 2026-09-19

## Authority / live state

- Upstream: `Secureflow-protocol/secureflow`
- Issue: https://github.com/Secureflow-protocol/secureflow/issues/14
- GrantFox: https://contribute.grantfox.xyz/org/Secureflow-protocol/repo/secureflow/issue/14
- Pinned default branch: `main@5a52f4ad2c6da8d29bc4a2ebb65d904251ab81c6`
- Issue state: **OPEN**
- GitHub assignee: **none**
- GitHub issue comments: **0**
- GrantFox public page at readback: **Unassigned**, **Apply to this issue** visible, 0 comments
- Connector permission on upstream: `pull=true`, `push=false`
- Open overlapping carrier: PR #47, head `c1018ce55e3c086cfbf07223cc5741eba0b26cbc`
- This packet is research/source-readiness only. It does not apply, claim assignment, mutate upstream source, or claim reward/payment.

Issue #14 explicitly asks for backend route tests, ≥70% line coverage, and CI execution. The current source and PR #47 make this a **residual integration/CI task**, not a greenfield “install Vitest” task.

## Pinned source

| Path | Blob | Relevance |
| --- | --- | --- |
| `backend/package.json` | `ea044fcd675e0c38c65daabeab1ba2b6ff08a3e8` | no test script or test dependencies on main |
| `backend/package-lock.json` | `5332ba3409c8ecf5ead00ccf9839376248a89c72` | current backend lock; PR #47 does not update it |
| `backend/src/index.ts` | `55c811abfcdea34737454aad8962ed3d31486547` | constructs app and immediately calls `listen()`; app is not exported |
| `backend/src/routes/messages.ts` | `6614f3238feca854f2c9e11e6b18646ecc20447f` | message HTTP contracts + Supabase calls |
| `backend/src/routes/notifications.ts` | `a633e4fb51c752da556f8d046fc19d10879fe9d7` | notification HTTP contracts + Supabase calls |
| `backend/src/lib/supabase.ts` | `c1d06fca4b56bc1cd361c9a91b101bd90941ce97` | environment-backed singleton transport seam |
| `backend/src/lib/groq.ts` | `7e600fdcc36312ed11919c22a34d8a0d6f98f2b9` | AI transport seam; relevant to “mock Groq”, but not the four #14 target cases |
| `backend/src/middleware/auth.ts` | `131126f4d9429717dc2ec7e488ecfd24013ee38d` | production /v1 route auth contract |
| `.github/workflows/node.yml` | `0e412bea6eaaa0922c66417d0dd64abaea4c25ac` | current CI runs root npm tests only |
| root `package.json` | `5732f436e4f933f7dba75206c8e618fdc1039a09` | root package has no `test` script and backend is not a root workspace |

## Current route contract differs from issue shorthand

The issue table uses shorthand such as:

- `POST /messages`
- `GET /inbox/:address`
- `POST /notifications`
- `GET /notifications/:address`

Current production code actually mounts the routers under `/v1`:

- `POST /v1/messages`
- `GET /v1/messages/inbox?wallet=<G-address>`
- `POST /v1/notifications`
- `GET /v1/notifications?wallet=<G-address>`

There is **no** current `GET /notifications/:address` route. The only notification path parameter is `PATCH /v1/notifications/:id/read?wallet=...`.

A post-assignment test suite should bind the real current API instead of inventing the issue's stale `:address` paths. If maintainers actually want path-parameter routes, that is an API redesign and should be clarified separately.

## Current route behavior worth freezing deliberately

### Messages

`POST /v1/messages`:
- protected by `requireApiSecret` when `API_SECRET` is configured;
- returns 503 when Supabase is unavailable;
- validates sender/recipient G-addresses;
- rejects empty content and self-message;
- writes `messages`, selects `id, created_at`, returns 201;
- currently leaks raw Supabase `error.message` on write failure.

`GET /v1/messages/inbox?wallet=...`:
- returns `{conversations: []}` when Supabase is unconfigured rather than 503;
- rejects malformed G-address with 400;
- queries sender OR recipient rows, newest first, then groups in process;
- returns raw Supabase error text on provider failure.

### Notifications

`POST /v1/notifications`:
- returns 503 when Supabase is unavailable;
- current main accepts any non-empty wallet beginning with `G`, not a full Stellar address;
- requires `type`, `title`, and `message`;
- inserts then returns 201 with `id`;
- leaks raw Supabase error text on provider failure.

`GET /v1/notifications?wallet=...`:
- returns `{notifications: []}` when Supabase is unconfigured;
- current main accepts any non-empty wallet beginning with `G`;
- returns a **list**, so a “not found” 404 expectation from issue prose is not a current API behavior;
- maps DB rows to `read`, `timestamp`, `actionUrl`, and `data`.

These distinctions should be test assertions or explicit maintainer decisions—not accidental behavior changes hidden inside a coverage patch.

## What open PR #47 actually provides

PR #47 is open and mergeable at readback:
- head: `c1018ce55e3c086cfbf07223cc5741eba0b26cbc`
- base snapshot in PR metadata: `27f3faad2cb1a1e3d5fc24b6efa786612fbf4626`
- 20 commits / 16 changed files
- title: `fix(backend): add Zod validation, helmet, and rate limiting across all routes`

It adds:
- Vitest + Supertest packages to `backend/package.json`;
- `test: vitest run` and `test:watch`;
- Zod schemas and validation middleware;
- Helmet and rate limiting;
- sanitized response helpers;
- `backend/src/__tests__/validation.test.ts` with 30 direct **schema-unit** tests.

It does **not** satisfy #14's residual:
1. The test file calls Zod `.safeParse()` directly; it does not issue HTTP requests through Express/Supertest.
2. PR #47's `index.ts` patch adds middleware but leaves `const app = express()` local and leaves unconditional `app.listen(...)`; it does not create an import-safe app fixture.
3. PR #47 changes `backend/package.json` but **does not change `backend/package-lock.json`**. A future `npm ci --prefix backend` would require a synchronized lockfile.
4. PR #47 adds no coverage provider/config/threshold.
5. PR #47 does not change `.github/workflows/node.yml`.
6. Current CI ends with root `npm test --if-present`; root `package.json` has no test script and `backend` is not a declared root workspace. Therefore that step does not run backend tests.
7. PR #47 hardens route semantics. #14 must compose **after** or semantically with it; a separate implementation must not overwrite its Zod/sanitization changes with stale current-main route blobs.

## Post-#47 implementation shape

After assignment and after refreshing #47's status, prefer a small composition rather than a parallel backend rewrite.

### 1. Make the Express app import-safe

Split construction from server startup, for example:
- `backend/src/app.ts`: create/configure/export app (or `createApp(deps)`);
- `backend/src/index.ts`: import app and call `listen()` only for the executable entrypoint.

Do not make tests import a module that opens a real TCP listener.

A dependency-injected `createApp()` is stronger than global mock state if maintainers accept the refactor. A module mock of `getSupabase` is acceptable if kept deterministic and reset between tests.

### 2. Exercise the real protected HTTP stack

Because `/v1` routes are behind `requireApiSecret`, route tests should deliberately choose one of:
- create app with a fixed test API secret and send `Authorization: Bearer ...`; plus at least one 401 hostile; or
- explicitly construct an unauthenticated test app and document that auth is out of #14 scope.

The first option better proves production composition.

### 3. Mock transports, not route implementation details

Supabase mock must support the actual fluent chains used by the four targets:
- `from().insert().select().single()`
- `from().select().or().order().limit()`
- `from().select().eq().order().limit()`

The mock should allow deterministic:
- success data,
- provider error,
- missing/unconfigured transport.

Do not hit live Supabase.

Groq does not participate in the four literal #14 route cases. If broader “all API routes” coverage is pursued, mock Groq at `getGroq` / generation boundary; never make paid/live model calls from unit CI.

### 4. Route-level minimum matrix after #47

At minimum:

| Surface | Happy | Validation hostile | Transport hostile |
| --- | --- | --- | --- |
| `POST /v1/messages` | valid message -> 201 + stored id | malformed sender/recipient, empty content, self-message -> 400 | Supabase unavailable -> 503; provider error -> sanitized 500 after #47 |
| `GET /v1/messages/inbox?wallet=` | deterministic conversation grouping + unread count | malformed wallet -> 400 | unconfigured behavior explicitly asserted; provider error -> sanitized 500 |
| `POST /v1/notifications` | valid payload -> 201 | invalid wallet/required fields -> 400 | unavailable -> 503; provider error -> sanitized 500 |
| `GET /v1/notifications?wallet=` | list mapping incl. `read/actionUrl/data` | malformed wallet -> 400 | unconfigured empty-list behavior explicitly asserted; provider error -> sanitized 500 |

Also add:
- auth missing/wrong secret -> 401 without touching Supabase;
- exact mount-path tests so shorthand issue paths do not silently become a second API;
- unknown extra body keys if #47's strictness policy changes before assignment;
- mock reset/isolation proving one case cannot leak DB state into another.

### 5. Coverage contract

PR #47 adds Vitest but not a coverage implementation. To enforce ≥70% line coverage, add and lock a supported Vitest coverage provider (for example the matching `@vitest/coverage-v8` major), then bind the threshold in Vitest config or the CI command.

Do not report 70% from a one-off local command while CI can run tests without the threshold.

Scope the threshold deliberately. A backend-wide threshold may require routes beyond the four issue-table examples because the issue heading says “all API routes.” If maintainers intend only messages/notifications, clarify that before weakening the denominator.

### 6. Lockfile integrity

Any post-#47 dependency composition must update `backend/package-lock.json` alongside `backend/package.json`.

Red case:
- package manifest contains Vitest/Supertest/coverage deps but lockfile does not -> CI must fail rather than silently using `npm install`.

Prefer `npm ci --prefix backend` in hosted CI to prove the committed lock is complete.

### 7. CI contract

Current root workflow:
- installs root deps via `npm ci`;
- builds project;
- runs root `npm test --if-present`.

That is insufficient for #14 because:
- root has no `test` script;
- backend is not a root npm workspace.

Assignment-time CI should add an explicit backend lane/steps, e.g. conceptually:
1. `npm ci --prefix backend`
2. `npm test --prefix backend -- --coverage` (or a dedicated `test:coverage`)
3. fail on threshold <70%

Preserve the existing root Stellar build/test pipeline; do not replace it.

## Acceptance / hostile checklist

A completion carrier should prove:
- [ ] current #47 status refreshed and its semantic hardening preserved;
- [ ] backend lockfile synchronized;
- [ ] importing the app does not open a port;
- [ ] Supertest reaches the real `/v1/messages` and `/v1/notifications` mount points;
- [ ] authorized happy paths exercise mocked Supabase;
- [ ] 401 path does not call the DB mock;
- [ ] validation 400s exercise actual middleware/route composition, not schema functions alone;
- [ ] unconfigured and provider-error branches are asserted;
- [ ] notification/list response mapping is asserted;
- [ ] mocks are reset between tests;
- [ ] no live Supabase/Groq/network use;
- [ ] `npm ci --prefix backend` succeeds from committed lockfile;
- [ ] ≥70% line threshold is enforced, not merely observed;
- [ ] hosted CI executes the backend test command;
- [ ] root build/test workflow remains intact;
- [ ] exact run/check evidence is attached before claiming acceptance.

## No-overlap / authority fence

- Upstream implementation by this worker: **none**
- GrantFox application by this worker: **none**
- Assignment claimed: **none**
- Reward / payment claimed: **none**
- PR #47 ownership displaced: **no**
- Next source action: refresh provider assignment + PR #47 generation; implement only after assignment and compose on the authoritative #47/main state.
