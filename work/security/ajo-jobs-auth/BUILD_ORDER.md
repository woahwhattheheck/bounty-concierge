# Security build order — Ajo `/api/jobs` authorization boundary

ID: `SEC-AJO-JOBS-AUTH-20260919`  
Discovered by: ZZ-Sol-Bounty-Ranger · GPT-5.6 Sol  
Observed upstream: `Ajo-contrib/soroban-ajo master@1b87ea7344ae3d871e54abff05eabe5113bd2956`

## Why this exists

This was discovered while auditing GrantFox issue #938. It is deliberately separated from Zod
strictness work: request validation is not authorization.

The internal GitHub issue creation route was attempted first and returned provider error
`410 Issues has been disabled in this repository`, so this build order is carried as a merged file
plus Slack routing instead of being trapped in one session.

## Exact source evidence

### Live application mount

`backend/src/index.ts` blob `1d1a94f0e3ae09de6de3e19701835f337516e897` mounts:

`app.use('/api/jobs', jobsRouter)`

The visible global middleware before that mount includes helmet, CORS, IP/DDoS protection,
request throttling/logging, body parsing, API rate limiting and API versioning. No global
authentication middleware is applied to `/api/jobs`.

### Router surface

`backend/src/routes/jobs.ts` blob `343db64012fd14ce022d584e36ede5c5ab8d248b`
does not import/apply an authentication or authorization middleware and exposes state-changing
operations including:

- retry a failed queue job;
- delete a queue job;
- enqueue email jobs;
- enqueue payout jobs;
- enqueue sync jobs;
- enqueue notification jobs;
- update payout schedule configuration;
- manually process due payouts;
- emergency force payout to a recipient;
- emergency skip current payout.

The emergency force route accepts recipient address/id, amount and cycle number. The emergency skip
route reads `req.body.reason` directly.

## Evidence boundary

This proves the **application source path lacks an observed auth/authz gate** on the pinned commit.

It does **not** by itself prove an internet-reachable production exploit. A reverse proxy, private
network, API gateway, WAF policy, VPN, mTLS boundary or other deployment-specific control may still
restrict the route. An authorized operator must confirm the deployed ingress before making an
external-exposure claim.

## Collision fence

At discovery time:

- exact Slack search found no claim/audit/build order for Ajo `/api/jobs` auth or emergency payout
  authorization;
- focused upstream open-issue and open-PR searches for jobs auth / emergency payout auth found no
  carrier;
- discovering connector is pull-only upstream, so no upstream mutation was made.

## Required repair

1. **Confirm intended route audience and deployment boundary.**
   Record whether `/api/jobs` is intentionally private/operator-only and what currently enforces it.

2. **Add source-level authentication at the router/mount.**
   Do not rely solely on network placement for state-changing financial/admin endpoints.

3. **Add operation-level authorization.**
   Suggested policy classes:
   - health/status: explicit intentionally-public decision or operator read role;
   - queue retry/delete/enqueue/schedule mutation: operator/admin;
   - emergency force/skip payout: strongest privileged role available.

4. **Audit emergency actions.**
   Persist actor identity, operation, target group/job, reason, request correlation id, timestamp and
   outcome. Never log private keys, secrets, bearer tokens or signed transaction material.

5. **Separate authz from request validation.**
   Add strict Zod body schemas to state-changing endpoints, but do not treat strict parsing as the
   authorization fix.

6. **Review route ordering.**
   Generic `/:queue/:jobId` patterns appear before payout-admin routes in the same router. Add
   routing tests so generic paths cannot shadow or reinterpret privileged endpoints.

7. **Confirm CSRF/browser credential model.**
   If browser cookies or ambient credentials can reach these endpoints, require appropriate
   anti-CSRF/origin controls.

8. **Document deployment defense in depth.**
   Source auth should remain authoritative even if gateway/network restrictions exist.

## Hostile regression matrix

- unauthenticated queue retry => 401 and retry service not called;
- unauthenticated queue delete => 401 and delete service not called;
- unauthenticated enqueue email/payout/sync/notification => 401;
- ordinary authenticated user => 403 for operator/admin actions;
- operator role cannot invoke emergency payout when stronger privilege is required;
- authorized emergency force payout succeeds and emits audit event;
- authorized emergency skip requires/records a bounded reason and emits audit event;
- denied emergency actions emit security/audit evidence without sensitive payloads;
- unknown body keys reject independently of auth outcome;
- malformed recipient/amount/cycle values reject before payout service invocation;
- generic dynamic routes cannot intercept `/payouts/...` privileged routes;
- if health/stats remain public, responses expose no secrets or job payloads;
- deployment test/documentation proves gateway/private-ingress rules are additive defense, not the
  sole authorization boundary.

## Suggested completion evidence

An authorized repair seat should publish:

- exact upstream base/head SHA;
- route/middleware diff;
- exact authn/authz policy by endpoint;
- focused test command and terminal result;
- full relevant backend test/type-check result;
- deployment-boundary evidence or an explicit “not verified” statement;
- PR URL/head and final merge/readback receipt if merge authority exists.

## Relation to existing GrantFox research

The Zod request-strictness packet for Ajo #938 is merged in
`woahwhattheheck/bounty-concierge` PR #395 as
`bcda39ec0cf0966a1fc2b83653f1fda8f14358cd`.

This build order is a separate security lane.
