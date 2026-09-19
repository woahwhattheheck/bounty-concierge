# GrantFox source baseline — Ajo-contrib/soroban-ajo #938

Operation: `GFOX3-20260919-ajo-938-R-zod-strictness`  
Worker: ZZ-Sol-Bounty-Ranger · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream default branch: `master@1b87ea7344ae3d871e54abff05eabe5113bd2956`

## Authority / collision fence

- Issue: https://github.com/Ajo-contrib/soroban-ajo/issues/938
- GitHub state at observation: OPEN, no assignee, one applicant comment.
- Focused open-PR search for #938 / validationUtils / Zod strictness surfaced no implementation carrier.
- Exact Slack census for repo + issue returned no prior TAKE/DONE before this research claim.
- Upstream connector permissions: `pull=true`, `push=false`; no upstream source mutation is authorized from this seat.
- No GrantFox application, wallet, payment, award, or reward state was mutated.
- This packet is source-readiness / security-audit evidence only. Provider-side assignment must be refreshed before implementation if required.

## Current Zod contract

Backend `package.json` blob `817d678882942193301248e5dac6b6fddabb82e8` pins `zod: ^3.22.4`.

Repository-wide GitHub code search on the pinned commit found 25 backend source files containing
`z.object(...)`, and zero occurrences of:

- `.strict()`
- `z.strictObject`
- `.passthrough()`
- explicit `.strip()`

For Zod v3, a plain `z.object()` strips unrecognized keys from the parsed output. `.strict()`
rejects them with an `unrecognized_keys` issue. Official v3 reference:
https://v3.zod.dev/?id=objects

Therefore the current request middleware generally does **not** pass unknown keys downstream; it
silently accepts the request and removes those keys. That is safer than passthrough but still fails
the #938 requirement to reject unexpected fields on security-sensitive ingress.

## Middleware behavior is mutation-by-parse

The live request helpers all replace request data with parsed output:

- `backend/src/middleware/validateRequest.ts` blob
  `c9a48f32c3fdb335c8f24ee5c5f90565901969a4`
- `backend/src/middleware/validationMiddleware.ts` blob
  `ba62b5b1ed6ccc19103482ba5205ddda90d741d9`
- `backend/src/utils/validationChain.ts` blob
  `b71c4a3dbef99c176fc4c74aafa38fe852e775be`

They assign `req.body/query/params = schema.parseAsync(...)`. With ordinary Zod objects, unknown
keys disappear silently. The right regression assertion is therefore not merely “extra field did not
reach Prisma”; the endpoint must return a validation error and never invoke the handler/service.

## validationUtils type/surfacing seam has drifted

Issue #938 mentions a type error around `ZodUnrecognizedKeysIssue`. Current
`backend/src/utils/validationUtils.ts` blob
`24cfd353fc3e578e86fe26addcde8e0a1df009a3` no longer refers to that type.

Instead:

- `getExpectedType(error: z.ZodIssue)` uses `any` casts for several variants.
- `formatValidationErrors` emits `path/message/code/received/expected`.
- It has no branch for `unrecognized_keys` and no typed `keys` field.
- The other live formatter, `backend/src/utils/zodHelpers.ts` blob
  `03bc2111acdd56267792a0e0c43f50f135710fa4`, likewise emits only
  `field/message/code`.

So the old type error is not reproducible from current source as described; the current source appears
to have sidestepped the seam rather than modeling the discriminated issue correctly. Once strict
schemas are added, tests should prove `unrecognized_keys` is surfaced consistently. Report key
**names only**; never reflect unknown values into logs/errors.

## Do not blanket-strict every object

A repository-wide mechanical replacement of every `z.object()` would be incorrect.

The 25-file census includes environment/config objects that parse `process.env`. Those inputs
legitimately contain unrelated keys. Strictness should target HTTP request contracts (and nested
security objects where a closed shape is intentional), not response schemas, environment schemas,
internal persisted records, or protocol objects that intentionally permit extension.

Use an explicit request-schema policy, e.g. a small helper or a documented requirement that
security-sensitive top-level request bodies use `.strict()` on Zod 3.

## High-risk live ingress map

### Authentication / 2FA

Pinned files:

- `backend/src/schemas/auth.schema.ts` —
  `7c5b0576e0c35f6036786d86b7d3687beccaad40`
- `backend/src/routes/auth.ts` —
  `057894815d4d2d945298892aa6fef481c9090aa9`

Live routes validate token creation, TOTP enable/disable, SMS setup/verify and recovery using ordinary
`z.object()` schemas. Add strict top-level contracts and hostile tests such as:

- token request with `isAdmin`, `role`, or unrelated credential fields;
- 2FA enable/disable with an extra recovery/secret field;
- recovery with unknown alternate identity fields.

Expected: 400 / stable validation code; handler and Prisma mutation are not reached.

### Group creation / contribution

Actual live `/api/groups` wiring imports from:

- `backend/src/validators/groups.ts` —
  `d61c83e74100337121170d755268253801c2fba5`
- route blob `backend/src/routes/groups.ts` —
  `0ec238de367579af2cd53771e66f4014069d7ed6`

The repo contains several other group-schema stacks, including
`schemas/group.schema.ts`, `middleware/validators/groupValidators.ts`, and
`schemas/validationSchemas.ts`. Hardening an unused duplicate is not sufficient.

Hostile tests should bind the actually mounted schemas for create/join/contribute. Unknown keys must
reject before controller/webhook invocation. Financial contribution bodies deserve first-class tests.

### Multisig

- `backend/src/types/multisig.ts` —
  `d57e75e2280ed710d36d1af6eab5988648e09bd9`
- `backend/src/routes/multisig.ts` —
  `8c45bea07b8987c3726894fc758bdefc4b523b7c`

Top-level config/proposal/sign objects are non-strict. `multiSigConfigSchema.signers[]` also uses a
nested non-strict object. Strictness should cover both top-level and signer entries; test injected
`admin`, alternate `weight`-like fields, and unrelated execution metadata.

`createProposalSchema.metadata` is intentionally `z.record(z.unknown())`; do not “strict” that
extension point by accident.

### Payout / job administration

- `backend/src/routes/jobs.ts` —
  `343db64012fd14ce022d584e36ede5c5ab8d248b`
- app mount `backend/src/index.ts` —
  `1d1a94f0e3ae09de6de3e19701835f337516e897`

`scheduleConfigSchema` and `forcePayoutSchema` are ordinary non-strict objects.
The emergency skip route reads raw `req.body.reason` with no Zod schema.

This file also exposed a larger, separate security concern during the audit: `/api/jobs` is mounted
directly without an app-level auth wrapper, while the route file contains state-changing retry/delete,
queue submission, schedule mutation, emergency force-payout and emergency skip-payout endpoints.
That is **not** folded into #938 as though strictness fixes authorization. It should be triaged as a
separate auth issue immediately.

### Notifications / push

- `backend/src/routes/notifications.ts` —
  `970e8057b06a2f3beff19835061e73f0a7bb3b62`

Reminder preferences, push subscriptions and unsubscribe bodies are non-strict. The nested push
`keys` object is also implicit-strip. Add top-level/nested unknown-key regressions where the protocol
shape is intended to be closed.

### Data export

- `backend/src/controllers/dataExportController.ts` —
  `86891370a7feb7da863ab0b441b80905189960b4`

`createExportSchema.safeParse(req.body)` silently strips unknown fields. Test attacker-controlled
identity/path/range override fields and require rejection before export job creation.

### Calendar custom events

- `backend/src/routes/calendar.ts` —
  `ac6ffc863bb8519eb92dd014bfcdeb84db75efe4`

Custom event bodies are non-strict. This is lower sensitivity than auth/payout but is still an HTTP
contract and should have an explicit unknown-key policy.

### E2E public-key publication

- `backend/src/routes/e2e.ts` —
  `1ae863d227c2da3413f4b318e171c089c19d4d29`

The top-level key-publication object is non-strict. The nested JWK object needs care: JWK is an
extensible standard and additional legitimate members may exist. Make the top-level request strict,
then either enumerate supported JWK members or explicitly document/validate a deliberate nested
extension policy. Do not accidentally break compatible JWKs with a blind deep-strict transform.

## Financial goal schemas are currently a re-enable hazard

Pinned:

- `backend/src/schemas/goal.schema.ts` —
  `60a0796ba0b336fd65452fcd5f162fce14cdd805`
- `backend/src/routes/goals.ts` —
  `afb32665f3f1cefca8f5cecd52d07b1ee4d74bb1`
- `backend/src/controllers/goalsController.ts` —
  `6a14ac7bbba2cdd354e50e0f6874929d2e90b95a`
- `backend/src/services/goalsService.ts` —
  `5f69781c1b80cf8a0662da9e5540007ba29f63cb`

`goal.schema.ts` defines create/update/affordability/projection Zod contracts, but
`routes/goals.ts` mounts none of them and the controller passes raw `req.body` to the service.

However, current `index.ts` comments out the entire goals router:
`// app.use('/api/goals', goalsRouter) // Temporarily disabled due to type errors`.

Therefore this is **not a live HTTP exposure on the pinned main**. It is a release/re-enable blocker:
before goals are re-mounted, wire the schemas into every body/param ingress, make security-sensitive
bodies strict, and add integration tests proving unknown fields reject.

## Recommended repair sequence

1. Add a typed request-strictness helper/policy for Zod 3 instead of changing every object in the repo.
2. Update error formatting to handle `unrecognized_keys` by discriminated narrowing and expose key
   names safely and consistently in both formatter stacks.
3. Harden the actually mounted auth + group contribution + multisig + payout/job + export request
   schemas first.
4. Add targeted strictness to notifications/calendar/E2E top-level request objects, with an explicit
   nested JWK policy.
5. Wire goal schemas into routes before ever re-enabling `/api/goals`.
6. Reduce duplicate group-schema stacks or add a test/registry that identifies which schema owns each
   route, so later hardening cannot land in a dead duplicate.
7. Triage the unauthenticated `/api/jobs` concern separately; strict parsing is not authorization.

## Hostile regression matrix

At minimum:

- auth token + extra `role` => reject; no user mutation;
- 2FA recovery + extra alternate identity => reject; no 2FA mutation;
- group create/contribute + extra payout/admin field => reject; controller not reached;
- multisig config + extra top-level admin field => reject;
- multisig signer + extra nested authority field => reject;
- proposal `metadata` retains its intentionally open contract;
- force payout + extra destination/authorization-like field => reject before scheduling;
- emergency skip receives a defined schema before use;
- export + extra user/path override => reject before export creation;
- push subscribe + unknown top-level and unknown nested key => explicit expected policy;
- custom calendar event + extra field => reject;
- E2E publish + extra top-level field => reject while documented JWK members remain compatible;
- goal create/update/affordability/projection reject extras in integration tests before router re-enable;
- `unrecognized_keys` error response contains only key names, not attacker-controlled values;
- strictness tests assert service/controller/Prisma mocks were not invoked on rejection.

## Evidence limits

This packet is based on exact connector readback of the pinned commit plus repository-wide code search.
The local runner did not have the upstream repository checkout/Zod package materialized, so no fresh
upstream Jest/type-check run is claimed. Zod v3 unknown-key behavior is confirmed against the official
v3 documentation, not inferred from a local package execution.

## Fleet disposition

- #938 research lane: READY / packet publication pending.
- Upstream implementation: not started.
- Upstream authority from this seat: read-only.
- GrantFox/provider assignment: not independently changed or claimed.
- Separate security follow-up discovered: live `/api/jobs` authorization boundary requires triage.
