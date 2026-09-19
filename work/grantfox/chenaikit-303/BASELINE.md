# GrantFox source baseline — nexoraorg/chenaikit #303

Operation: `GFOX3-20260919-nexoraorg-chenaikit-303-R-LANTERN`  
Worker: ZZ-Sol-17-Lantern · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `2653a3f1d670828068c3c3692d2c7834d2e8bf15`

## Provider / assignment fence

- Issue: https://github.com/nexoraorg/chenaikit/issues/303
- GrantFox: https://contribute.grantfox.xyz/org/nexoraorg/repo/chenaikit/issue/303
- GitHub issue: OPEN, unassigned, 1 comment.
- GrantFox: Unassigned, Apply visible, one application per user, one visible application.
- Issue explicitly says: comment to get assigned and do not open a PR for an unassigned issue.
- Connector authority on upstream: pull=true, push=false.
- Fresh open-PR search for #303 surfaced no carrier.
- Reward is possible/discretionary; no assignment, award, amount, or payout is asserted.

This packet is pre-assignment source research only.

## Current backend surface

The issue text sounds broad (“schemas for public request bodies and parameters”),
but current main exposes a much smaller live backend surface than that language
suggests.

Pinned backend `apps/backend/src/app.ts` blob:
`1717213a3a144150cfb91f5da5f639b53bcb7b5c`.

The app currently exposes:
- `GET /health`;
- `GET /api/records`;
- `POST /api/records`;
- test-only `GET /api/trigger-error`.

The only public mutating route surfaced in the live app is
`POST /api/records`.

That route already performs inline validation before persistence:
- `name` must be a non-empty string;
- `value` must be a number and not NaN;
- only after both checks does it call `prisma.apiRecord.create`.

So the original failure mode is partially addressed for the current write route:
malformed `name`/`value` does not intentionally reach Prisma. The missing
issue-grade work is **centralized, reusable boundary validation with one error
contract and side-effect proof**, not merely adding two more if-statements.

## Existing error contract that the validation layer should reuse

The repository already has a mature typed error vocabulary.

`apps/backend/src/errors/AppError.ts` blob
`e845339e81f6d39b9f7250e02cee63df1340a32b` defines:

- `ValidationError`;
- stable code `validation_error`;
- HTTP 400;
- optional safe structured `details` specifically documented for field failure
  context.

The central error middleware blob
`cf73f7ed6bc4f0980d5598296aa07b16718170ba` serializes known errors through
`buildErrorBody`, preserving stable code/message/requestId/details.

Backend README blob `996e7ebd117050a4b669aaeb1fcd3a8ab8a22b4b`
documents that contract for clients.

### Current inconsistency

`POST /api/records` bypasses the typed error contract and directly returns:

```json
{
  "error": "Validation Error",
  "message": "Field 'name' is required and must be a non-empty string."
}
```

(or the analogous `value` message).

That means validation today:
- is route-local;
- has a display label rather than the documented machine code;
- does not include the normal requestId/details shape;
- cannot be reused by future body/query/param routes without copy/paste.

Issue #303 is therefore a good fit for **normalizing validation into the existing
AppError/errorHandler architecture**, rather than introducing a parallel error
stack.

## Current test contract

Integration test
`tests/integration/backend-api.test.ts` blob
`8fd7bd442ab4831ede4c12a49377b8549c9672fe` already covers:
- valid POST persists and can be read back;
- missing `name` returns 400;
- non-numeric `value` returns 400;
- malformed JSON stays a 400 path;
- persistence/server failure gets the expected error behavior.

However, the tests assert the legacy literal `error: "Validation Error"`.
An assigned implementation must deliberately update that compatibility contract
if it moves the route onto `ValidationError`; it should not accidentally break
clients and call that “middleware.”

The strongest new regression proof is not just status/body matching: instrument
or mock `prisma.apiRecord.create` and prove invalid requests leave its call
count at zero.

## Dependency decision

Backend package blob:
`eeef2b3912c9fefc18fb8baa16349266be265b90`.

There is currently no Zod/Joi/Ajv dependency. Issue #303 says “add the dependency
only if needed.” The present route surface is tiny enough that a repository-local
schema/middleware abstraction can meet the acceptance criteria without creating
a new runtime dependency.

A sensible assigned-seat sequence is:

1. Define a small typed schema contract for body/query/params in
   `apps/backend/src/validation` or `middleware/validation`.
2. Implement one middleware factory that:
   - reads the declared request location(s);
   - accumulates field issues or follows a documented first-error policy;
   - throws `ValidationError` with safe field details;
   - writes normalized values back only after the complete schema succeeds;
   - calls `next()` exactly once on success.
3. Migrate `POST /api/records` first.
4. Add a third-party schema library only if the actual route matrix grows beyond
   the small local abstraction and the PR explains the tradeoff.

Do not introduce two validation systems (inline + library) indefinitely.

## Validation semantics for current route

For `POST /api/records`:

### name
- required;
- must be a string;
- trim before persistence;
- reject empty or whitespace-only;
- keep a documented maximum length if Prisma/domain policy supplies one; do not
  invent a storage bound without schema/product evidence.

### value
- required;
- must be a JSON number;
- must be finite;
- reject NaN/Infinity at programmatic boundaries even though valid JSON cannot
  encode them;
- preserve the current valid numeric behavior, including ordinary floats.

Unknown-field policy should be explicit. Current route ignores extra fields
because it destructures only `name` and `value`. Changing to strip/reject
unknowns is an API behavior decision and should not happen accidentally.

## Error response contract

Prefer the already-documented application shape:

```json
{
  "error": "validation_error",
  "message": "The request could not be validated.",
  "requestId": "...",
  "details": {
    "fields": {
      "name": "required"
    }
  }
}
```

The exact field-detail structure can vary, but it should be:
- stable;
- machine-readable;
- safe for clients;
- covered by tests;
- produced by the same central error handler as other known errors.

Malformed JSON is parser-level validation. Preserve its current 400 behavior and
decide whether it should also normalize to `validation_error`; do not let a new
body schema turn malformed JSON into 500.

## Side-effect fence

Acceptance says invalid input must be rejected before handler logic and
persistence. The proof should be explicit:

- middleware is mounted before the async handler;
- invalid body => handler sentinel/counter remains untouched;
- invalid body => `prisma.apiRecord.create` is not called;
- valid normalized body => handler called exactly once and persistence receives
  trimmed `name` + unchanged valid number;
- schema/middleware failure cannot call `next()` into the handler after sending
  a response.

This is stronger than merely observing a 400.

## Future public request inventory rule

At this pinned main, there are no other live body/query/param-heavy backend
routes surfaced by the app. The assigned PR should not create speculative
schemas for nonexistent endpoints.

Instead:
- build the middleware as reusable for `body`, `query`, and `params`;
- migrate the real record POST;
- add representative middleware unit tests for each request location if the
  abstraction supports them;
- when future public routes land, require a schema at route declaration time.

This keeps the PR scoped under the issue's ~600-line reviewability guidance.

## CI / repository constraints

Backend CI workflow blob:
`9232e4c9e3a24c81ac87c0109f999bbf065ab972`.

Relevant checks:
- pnpm 9.15.9, Node 22;
- Prisma generate;
- backend lint;
- `tsc -p tsconfig.json --noEmit`;
- Vitest;
- integration tests;
- backend build;
- shared package build job.

Backend tsconfig blob:
`a7381e28d9986d768446368ba347cad9dd220b3d`, strict TypeScript/NodeNext.

Issue also requires branch naming/conventional commits, complete PR template,
one approving review, and current main before merge.

## Hostile test matrix

1. missing body / `undefined` body.
2. missing `name`.
3. null `name`.
4. numeric/object/array `name`.
5. whitespace-only `name`.
6. missing `value`.
7. null/string/object/array `value`.
8. programmatic NaN/Infinity guard at schema unit level.
9. valid trimmed name + float persists unchanged semantically.
10. malformed JSON remains a 400 rather than 500.
11. invalid request leaves Prisma create call count at zero.
12. invalid request leaves handler sentinel untouched.
13. valid request calls handler/persistence exactly once.
14. field error identifies the exact field through structured details.
15. unknown-field behavior is frozen to the compatibility choice.
16. requestId remains present when routed through the central error handler.
17. existing error-handler and Prisma-error tests stay green.

## Provider-ready application angle

The one visible existing application is generic. A source-specific application
can say:

> I pinned current main and found the issue is partially implemented: the only
> live public mutating route, POST /api/records, already hand-validates name and
> value before Prisma, but it bypasses the repository's existing typed
> ValidationError/errorHandler contract and returns a legacy "Validation Error"
> body. I would centralize boundary validation around the existing
> validation_error + details shape, migrate the real route without inventing
> schemas for nonexistent endpoints, and prove malformed/missing input never
> invokes the handler or prisma.apiRecord.create. I would preserve malformed-JSON
> 400 behavior, explicitly freeze unknown-field compatibility, and run backend
> lint/typecheck/Vitest/integration/build under the existing pnpm/Node 22 CI.

Wait for assignment after application; the issue explicitly forbids an
unassigned PR.

## Fleet disposition

- Research lane: complete.
- Upstream implementation: not started.
- Assignment gate: hard requirement from issue text.
- Open carrier: none surfaced.
- Recommended next: authenticated provider application using this source-specific
  plan; implement only after durable assignment.
