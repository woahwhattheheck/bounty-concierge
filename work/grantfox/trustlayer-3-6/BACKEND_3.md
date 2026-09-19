# TrustLayer Backend #3 — canonical signal validation baseline

Source-only, pre-assignment GrantFox readiness packet.

- Upstream: `TrustLayer-Org/TrustLayer-Backend`
- Issue: https://github.com/TrustLayer-Org/TrustLayer-Backend/issues/3
- GrantFox: https://contribute.grantfox.xyz/org/TrustLayer-Org/repo/TrustLayer-Backend/issue/3
- Pinned upstream main observed: `6ba347951ab6e99e46c4983b2e15f370f214e15f`
- Live issue state at census: OPEN; no assignee; Development shows no branches or pull requests.
- Provider application: not claimed submitted by this packet.
- Upstream implementation: not started; maintainer assignment is required by the issue.

## Current source facts

### `src/signals/validate.js`

`validateSignal` only validates:

- `businessId` is a positive integer;
- `signalType` is a non-empty string and one of `payment|review|dispute|kyc`;
- `value` is a finite number.

It does not establish a strict request shape, reject unknown own keys, define a plain-object/prototype contract, canonicalize IDs across transport surfaces, or enforce a documented input range.

### `src/signals/router.js`

POST `/signals` validates `req.body` and then forwards the original body to storage/idempotency. Path/query IDs and pagination values are independently converted with `Number(...)`, so body/path/query identity parsing is not one shared contract.

### `src/signals/repository.js`

SQLite persistence writes only `businessId`, `signalType`, and `value`. Unknown POST fields therefore do not currently become columns, but accepting them at the HTTP boundary still creates ambiguous API semantics and can affect fingerprint/canonicalization behavior.

### Existing abuse controls

`src/app.js` already applies a configurable Express JSON body-size limit (100 KB default). Existing router coverage proves an oversized body gets 413. Preserve this substrate rather than replacing it.

### Coverage gap

`validate.test.js` has only the valid case plus non-positive business ID, unknown type, and nonnumeric value. Existing router tests cover rate limiting, idempotency, and body size, but not:

- unknown/prototype keys;
- malformed or non-plain bodies;
- Unicode/whitespace policy;
- path/query/body ID parity;
- numeric input boundaries;
- property/fuzz validation.

No current signal import/correction endpoint was found in the source tree/search. The issue's "shared by create, import, correction" requirement should therefore produce a reusable canonicalizer that future/current alternate mutation paths can consume, not invented unrelated endpoints.

## Assignment-ready implementation contract

1. Introduce a reusable canonicalization result: validated sanitized signal + stable structured errors.
2. Require a plain request object and an exact own-key allowlist; explicitly reject prototype-sensitive/unknown fields.
3. Centralize positive-ID parsing/canonicalization and use it on body, path, and query surfaces.
4. Keep signal type allowlisting centralized.
5. Resolve the numeric `value` range as an explicit maintainer-approved domain policy. Current README says values are numeric and final scores are clamped to 0..100, but that output clamp is not itself a documented input-range contract.
6. Fingerprint and persist the same canonical sanitized signal.
7. Add table/property tests for hostile keys, wrong types, Unicode/whitespace, numeric edges, malformed JSON, and transport-surface parity.
8. Document stricter unknown-field and ID canonicalization behavior as compatibility changes.

## Safety / authority

This evidence does not confer GrantFox assignment, reward eligibility, upstream write authority, payment authority, or permission to implement before maintainer assignment.
