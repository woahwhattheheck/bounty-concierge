# Implementation plan — Credence API drift protection (#1045)

This plan is written against `CredenceOrg/Credence-Frontend@430e4439c8f6434e624c4c0919a04cb8a3a0f38c`.

The goal is not merely "generate types." That already exists. The goal is to make four things true at once:

1. the committed generated file is reproducible from the committed OpenAPI contract;
2. production consumers bind to named operations instead of merely compatible schema aliases;
3. malformed successful responses are rejected at the API boundary;
4. a breaking contract change fails a targeted, deterministic gate.

## Recommended change set after provider assignment

### 1. Add a deterministic verification script

Add package scripts conceptually equivalent to:

```json
{
  "scripts": {
    "generate:api": "openapi-typescript openapi.yaml -o src/api/generated.ts",
    "verify:api-generated": "npm run generate:api && git diff --exit-code -- src/api/generated.ts",
    "test:api-contract": "vitest run <focused contract test files>"
  }
}
```

Do not add `git checkout` or other self-healing behavior. A drift check must fail and show the diff; it must never mutate the workspace back to green.

CI must run after `npm ci`, so the lockfile determines the generator version.

### 2. Add a focused API-contract CI job without unpausing unrelated gates

The existing quality gate is intentionally stubbed. #1045 should add a narrow job such as:

```yaml
api-contract:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@<pinned-major-or-sha>
    - uses: actions/setup-node@<pinned-major-or-sha>
      with:
        node-version-file: ...
        cache: npm
    - run: npm ci
    - run: npm run verify:api-generated
    - run: npm run test:api-contract
    - run: <focused TypeScript contract check>
```

Do not silently replace the paused broad job with a full repo build in the same bounty unless current main is first proven green. The contract job should be independently actionable and deterministic.

### 3. Bind trust-score fetching to the actual operation

Current:

```ts
import type { TrustScore } from '../api/types'

const result = await apiFetch<TrustScore>(
  `/trust-score/${encodeURIComponent(targetAddress)}`,
  ...
)
```

Target:

```ts
import type { ApiResponse, operations } from '../api/types'

type GetTrustScoreResponse = ApiResponse<operations['getTrustScore']>

const result = await apiFetch<GetTrustScoreResponse>(...)
```

This makes response-field or enum changes in the named OpenAPI operation visible to the consumer.

For stronger path coupling, prefer a small endpoint wrapper whose implementation and type live together, e.g. `src/api/trustScore.ts`, rather than letting every hook manually spell a path string.

### 4. Do not type a fictional bond endpoint

The OpenAPI contract has a `Bond` schema but no bond path.

Before creating a real bond API wrapper, obtain/land the authoritative operation in `openapi.yaml`. The accepted endpoint must define:
- method and path;
- operationId;
- path/query/body parameters;
- request schema;
- success response status/body;
- error responses;
- exact amount representation;
- relevant authentication/tenant semantics.

Until then:
- schema-level Bond fixture tests are valid;
- an operation-level `operations['createBond']` test is impossible because that operation does not exist;
- a handwritten `apiFetch<Bond>('/bonds')` would recreate the drift problem #1045 is meant to remove.

### 5. Add runtime response validation

`apiFetch<T>` performs JSON parsing, not structural validation. TypeScript generics disappear at runtime.

Two implementation patterns are defensible.

#### Pattern A — endpoint decoders at the boundary

Create endpoint-specific decoders such as:
- `decodeTrustScore(value: unknown): GetTrustScoreResponse`
- `decodeTransactionList(value: unknown): ListTransactionsResponse`

They should reject malformed 2xx payloads with a typed `ApiContractError`.

The validator implementation should be visibly tied to generated types. One no-dependency strategy is:
- validate object/array primitives;
- validate every required property;
- validate enum membership;
- validate numeric/string expectations;
- use compile-time exhaustiveness helpers so generated required-key changes force validator edits.

This is small for the current contract but requires discipline as the API grows.

#### Pattern B — generated/schema-driven runtime validator

If the maintainers are willing to add a runtime/schema validation dependency, generate validators from the same OpenAPI source and make endpoint wrappers validate against them.

This better minimizes hand-maintained drift, but dependency weight/toolchain fit must be reviewed. Do not add a validator ecosystem merely because TypeScript itself cannot validate runtime bytes.

### 6. Make malformed 2xx responses fail closed

Contract errors should be distinguishable from:
- HTTP errors;
- network errors;
- abort/cancellation;
- session conflicts.

Example semantic error:

```ts
class ApiContractError extends Error {
  readonly path: string
  readonly operationId: string
  readonly issues: readonly ContractIssue[]
}
```

The error must not log sensitive payload bodies by default. A bounded issue list such as "missing field tier" or "expected number at score" is enough.

A malformed successful response should never be cached as trusted domain data.

## Contract fixtures

### Trust-score valid fixture

Create a canonical successful fixture using the existing contract fields:
- valid synthetic Stellar address;
- numeric score;
- one of `bronze | silver | gold | platinum`;
- integer attestation count;
- ISO-like timestamp string.

The fixture should type-check against `ApiResponse<operations['getTrustScore']>` and pass the runtime decoder.

### Trust-score hostile fixtures

At minimum:
- missing `address`;
- missing `tier`;
- unknown `tier`;
- string `score`;
- negative `attestations` if the decoder enforces the OpenAPI minimum;
- non-object JSON;
- invalid or missing `updatedAt` if format validation is part of the chosen runtime bar.

Each should produce a contract error before React state is updated.

### Transaction-list hostile fixtures

Existing `useTransactions` is operation-typed but still trusts runtime JSON.

Test:
- `items` missing;
- `items` not an array;
- item missing required `hash`;
- unknown transaction `type`;
- unknown transaction `status`;
- malformed `nextCursor` type.

### Bond fixture

Because no bond operation exists yet:
- type a representative object against `components['schemas']['Bond']`;
- test required fields and enum values at the schema/decoder layer only;
- mark operation-level request/response fixture tests `BLOCKED_BY_CONTRACT`, not TODO-as-if-an-endpoint-were-known.

Once an authoritative bond operation lands, convert this to `ApiResponse<operations['...']>` and add request/response tests.

## Breaking-change tests

The acceptance criterion says a breaking API change must produce a visible compile or contract failure.

A robust test matrix should include:

| Contract mutation | Expected gate |
| --- | --- |
| change trust-score `tier` enum | generated diff + consumer/decoder compile/test impact |
| remove required trust-score field | generated diff + decoder/fixture failure |
| change score from integer/number to string | generated diff + compile/runtime fixture failure |
| rename `getTrustScore` operationId | compile failure in operation-typed consumer |
| change `/trust-score/{address}` response schema | generated diff + focused contract tests |
| edit OpenAPI but forget generated.ts | `verify:api-generated` fails |
| hand-edit generated.ts without matching OpenAPI | regeneration diff fails |
| return malformed 200 JSON | endpoint wrapper raises contract error |
| add optional response field | generation diff visible; existing consumer should remain green |

Do not rely only on snapshot text. At least one test should prove the generated-diff command returns non-zero when `openapi.yaml` and `generated.ts` diverge.

## Compile-scope design

Because the broad CI job is paused, create a focused contract typecheck if a full `npm run build` is not currently reliable.

Good options:
- a dedicated `tsconfig.api-contract.json` extending the project config and including the API type layer + endpoint wrappers + selected hooks/tests;
- `tsc` over a small compile fixture that imports the generated operations and production wrappers.

The chosen scope must include the production consumers whose operation types are meant to drift-break. A typecheck that compiles only `generated.ts` proves almost nothing.

Once the repository-wide build is healthy again, the focused job can remain as a fast signal and the broad build can provide defense in depth.

## Path typing: next-level improvement

The current client accepts an arbitrary `string` path and an arbitrary generic `T`, which allows combinations that compile but are semantically unrelated:

```ts
apiFetch<TrustScore>('/some-other-path')
```

#1045 can be closed without rewriting the whole client, but a future improvement is an operation-aware wrapper where path/method/response types are selected from generated `paths`. That eliminates a class of "right type, wrong URL" bugs.

Do not expand #1045 into a large client rewrite unless maintainers approve it.

## Exact validation sequence after implementation

From a clean assigned branch:

```bash
git status --short
npm ci
npm run generate:api
git diff --exit-code -- src/api/generated.ts
npm run test:api-contract
<focused tsc command>
git status --short
```

Then deliberately prove the guard:

1. save hashes/status;
2. make a temporary breaking edit to `openapi.yaml`;
3. run `npm run verify:api-generated` or generate without committing output;
4. confirm non-zero drift/failure;
5. revert the temporary mutation;
6. rerun clean validation;
7. record exit codes.

No temporary mutation should be committed.

## PR review checklist

- [ ] Source base SHA recorded.
- [ ] `npm ci` uses the checked-in lock.
- [ ] `generate:api` remains one deterministic command.
- [ ] CI regenerates and checks `git diff --exit-code -- src/api/generated.ts`.
- [ ] Generated file is not hand-edited.
- [ ] `useTrustScore` (or its endpoint wrapper) uses `operations['getTrustScore']`.
- [ ] Existing `useTransactions` operation typing remains intact.
- [ ] Successful malformed JSON is rejected before domain/UI state.
- [ ] Contract errors do not dump raw sensitive payloads.
- [ ] Trust-score valid + hostile fixtures exist.
- [ ] Transaction-list malformed fixture coverage exists.
- [ ] Bond operation is not invented; schema-only coverage is clearly labelled until authoritative path exists.
- [ ] At least one deliberate drift mutation is proven to fail.
- [ ] Focused CI does not require pretending the paused broad quality gate is healthy.
- [ ] No unrelated UI refactor.

## Suggested commit structure

1. `ci(api): fail on stale OpenAPI generated types`
   - scripts + focused CI job
2. `refactor(api): bind trust score to generated operation type`
   - endpoint response alias/wrapper
3. `feat(api): reject malformed successful contract responses`
   - decoder + typed error
4. `test(api): cover trust score drift and malformed responses`
   - fixtures + hostile tests
5. Bond operation/fixture change only after the contract dependency is resolved.

This split keeps the assignment reviewable and gives maintainers a clean stop point if the Bond contract requires a separate backend decision.

## Non-goals

- Re-enable every paused repo-wide CI check.
- Repair unrelated existing build/test debt.
- Replace the transport client wholesale.
- Invent backend endpoints.
- Turn TypeScript type assertions into claims of runtime validation.
- Claim a bounty completion before provider assignment and upstream implementation/review.
