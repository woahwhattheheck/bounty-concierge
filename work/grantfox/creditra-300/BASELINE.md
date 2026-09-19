# Creditra-Backend #300 — credit-line authorization matrix source baseline

Status: source-audited; GrantFox/maintainer assignment required before upstream implementation.

## Target

- Upstream: `Creditra/Creditra-Backend`
- Issue: #300 — **[GrantFox][High] Enforce a complete credit-line authorization matrix**
- Upstream source generation: `main@70189f4a6e52f53c764123df760c2a1de39876ce`
- GitHub issue: OPEN, unassigned, 5 comments
- Open PR census matching issue #300: 0
- Live GrantFox: Apply enabled, 1 application per user, Direct GitHub comment, Assigned to **Unassigned**, 5 visible applicant comments
- Exact same-day Slack Creditra/#300 search before TAKE: 0 current claim results
- Upstream permissions from connected account: pull=true, push=false
- Reward truth: Maybe Rewarded / GrantFox OSS / Third Campaign / priority:high. No payout amount or award is asserted here.

This packet performs no upstream source mutation, provider application, assignment, credential, wallet, payment, or reward action.

## Executive source finding

The issue is not merely asking for a few missing route guards. The pinned source does not currently have an authenticated borrower/lender principal model that could enforce the requested borrower/lender/admin/service matrix.

The credit router exposes **seven mutation routes**:

| Method + path | Current auth | Current identity/ownership behavior |
|---|---|---|
| POST `/api/credit/lines` | none | caller supplies target `walletAddress`; repository create is called directly |
| PUT `/api/credit/lines/:id` | none | caller can patch `creditLimit`, `interestRateBps`, and `status` |
| DELETE `/api/credit/lines/:id` | none | hard delete by id |
| POST `/api/credit/lines/:id/draw` | none | caller supplies `walletAddress`; route calls submission function without authenticated principal |
| POST `/api/credit/lines/:id/repay` | none | caller supplies `walletAddress`; route calls submission function without authenticated principal |
| POST `/api/credit/lines/:id/suspend` | `X-Admin-Api-Key` | one global operator secret; no named actor/tenant context |
| POST `/api/credit/lines/:id/close` | `X-Admin-Api-Key` | one global operator secret; no named actor/tenant context |

So five of seven credit-line mutations are unauthenticated and the remaining two prove only possession of a global admin secret.

## Source evidence

### 1. Router-level authorization is incomplete

`src/routes/credit.ts` (blob `8bff0b996b0fee21ae65d7ceee4b9296dcf921a3`) documents and registers the entire credit surface.

Only `suspend` and `close` include `adminAuth`. Create, update, delete, draw, and repay have no authentication/authorization middleware.

The router is mounted in production by `src/index.ts` (blob `09eb326a96de5d571c66ab9d439404361c3bc980`) as:

```ts
app.use("/api/credit", defaultRateLimit, tenantMutationRateLimit, creditRouter);
```

Neither rate-limit middleware authenticates a principal, so there is no hidden global credit authorization layer.

### 2. No authenticated borrower/lender principal exists in request context

A pinned-source code search for `req.user` returns no matches.

`src/middleware/auth.ts` (blob `922ea281bf33595861d0a1d7bf399e45337fce3c`) checks whether an `X-API-Key` appears in a set, but it does not attach a role, tenant, subject id, or wallet identity to the request.

`src/middleware/adminAuth.ts` (blob `c94f02a84250313319cd4c0e0847a9e81bb60d65`) checks one `ADMIN_API_KEY` value and likewise attaches no actor identity.

`docs/SECURITY.md` (blob `c2be34bed37a32e9fa3b3e7c22ca9ee1465375c0`) explicitly says no JWT or cookie auth ships today and currently documents only:
- partner/integration API key for risk/reconciliation administration;
- operator admin key for credit-line suspend/close.

Therefore a real borrower/lender/admin/service matrix first needs a trusted **principal contract**. It must not infer authorization from caller-provided `walletAddress`, `x-tenant-id`, URL ids, or possession of a generic integration key.

### 3. Tenant rate limiting is not tenant authentication

`src/middleware/tenantRateLimit.ts` (blob `66deec2c2c52ee6aa81419d6c632eebede393f78`) defaults tenant identity from the caller-controlled `x-tenant-id` header and subject class from the mere presence of `Authorization`.

That is acceptable as a throttling key, but it is not an authorization principal and cannot be reused as proof of tenant membership. The route matrix must keep rate-limit identity separate from security identity.

### 4. Draw/repay actor attribution is caller-controlled

`src/services/creditService.ts` (blob `8b356d98517eca194cfa0077d01f05006ab3d185`) contains two service generations.

The repository-backed `CreditLineService.draw()` at least checks a supplied `borrowerId` against `line.walletAddress`, but the HTTP draw route does **not** call that method. It calls `submitDrawRequest()`, which takes `body.walletAddress` and records it as both `tenantId` and `actor`.

`submitRepayRequest()` does the same.

Because `walletAddress` is request body data, a caller can choose the audit actor label. This is not an authenticated borrower identity.

The repository-backed `CreditLineService.repay()` also ignores its `_walletAddress` parameter entirely, so even moving the current route onto that method would not create borrower ownership enforcement for repay.

### 5. CRUD mutations have no audit principal

`src/services/CreditLineService.ts` (blob `84bd11fd2b448040b30b0b0425d2db05e68d8418`) implements create/update/delete without audit-ledger calls or actor context.

The functional transition path records status-change transactions through the audit helper, but privileged transition records use generic/system-derived identity rather than the authenticated admin operator.

`src/services/auditLedger.ts` (blob `c87b7e58eff8e0fffcf64d0c420837078f282597`) already requires non-empty `tenantId` and `actor`, so the ledger can support the bounty's "privileged service paths are auditable" criterion once route authorization produces a trusted principal.

### 6. Existing tests deliberately bypass the real admin boundary

`src/__test__/creditRoute.test.ts` (blob `99680bec97cec5fbf1334436fa0dc3214e17f3be`) mocks `adminAuth` and defaults it to allow. It has useful suspend/close denial checks, but it cannot prove the real middleware + route matrix end to end.

`src/__tests__/credit.test.ts` (blob `ebe963fb8df0b61a8500ea067493676b43870226`) exercises the real admin header for suspend/close but does not cover authorization for create/update/delete/draw/repay because those routes have no corresponding rules today.

## Implementation architecture fence

Do not solve #300 by sprinkling unrelated header checks into seven handlers. The acceptance criteria explicitly require a complete matrix and shared enforcement.

A safe assigned design should introduce four separable concepts:

1. **Authenticated principal**
   - One request context shape such as `{ subjectId, role, tenantId, walletAddress?, authnMethod }`.
   - Populated only from trusted credentials/verification.
   - Existing admin/integration keys can map to explicit admin/service principals.
   - Borrower/lender authentication requires a maintainer-approved trust mechanism; do not treat request body wallet addresses as authenticated identities.

2. **Authorization policy**
   - A declarative route/action matrix keyed by a stable action id, not duplicated conditionals.
   - Every credit mutation must map to exactly one explicit rule.
   - The matrix should state role requirements plus ownership/tenant predicates separately.

3. **Shared middleware**
   - Authenticate, then authorize before mutation handlers.
   - Denials must happen before state-changing service calls.
   - Cross-tenant/wrong-role denials must not reveal whether a target line exists, its owner, limit, utilization, or state.

4. **Trusted audit attribution**
   - Record authenticated subject/role/tenant and stable action id for accepted privileged/service mutations.
   - Record enough denial metadata for security operations without logging keys/secrets.
   - Never use caller-supplied wallet or `x-tenant-id` as the audit actor unless it has been cryptographically/credential-bound to the authenticated principal.

## Route-inventory contract

The bounty's inventory test should fail closed when a new mutation is added without policy.

A robust test can derive or enumerate Express credit-router mutation registrations and compare them against the policy table:

- `POST /lines`
- `PUT /lines/:id`
- `DELETE /lines/:id`
- `POST /lines/:id/draw`
- `POST /lines/:id/repay`
- `POST /lines/:id/suspend`
- `POST /lines/:id/close`

The test must fail for:
- a route missing from the matrix;
- a matrix entry with no route;
- duplicate/ambiguous action ids;
- mutation route accidentally downgraded to public;
- middleware ordering that authorizes after a mutating handler.

Do not infer final role ownership for create/update/delete/repay from this packet. That domain policy is not fully specified by current source. The assigned implementation should make maintainers' chosen ownership explicit rather than silently inventing it.

## Required negative matrix

For every mutation, cover at least:
- unauthenticated;
- authenticated wrong role;
- right role / wrong tenant;
- right role / wrong borrower or owner where ownership applies;
- unknown target;
- valid same-tenant authorized request;
- malformed credential;
- service credential on a non-service action;
- admin credential on any intentionally non-admin action if policy forbids role escalation.

For the non-disclosure acceptance criterion, compare denied responses for existing vs nonexistent cross-tenant targets. Status/body should not become a resource-existence oracle.

## Compatibility and migration

Because public clients may currently call unauthenticated mutations, #300 is a security boundary change. The PR should:
- document which routes become protected;
- update OpenAPI/security docs atomically;
- avoid silently accepting legacy unauthenticated behavior;
- preserve existing public read behavior unless maintainers explicitly expand scope;
- keep `tenantMutationRateLimit` as throttling, not authorization;
- identify any internal reconciliation/indexer/service callers that need explicit service principals.

## Assignment fence

Before implementation, re-census GrantFox assignment, issue comments, current `main`, and issue-linked PRs. Upstream is pull-only for this account. Wait for maintainer assignment as the issue explicitly requires.
