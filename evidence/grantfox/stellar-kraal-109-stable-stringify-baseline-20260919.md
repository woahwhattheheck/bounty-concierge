# Stellar-kraal #109 — stableStringify / fingerprint test baseline

**Work order:** GFOX3-20260919-stellar-kraal-stellar-kraal-contract-109  
**Worker:** ZZ-Sol-Marmot-26  
**Model:** GPT-5.6 Sol  
**Observed:** 2026-09-19  
**Disposition:** SOURCE_ALIGNED / assignment-gated implementation

## Pinned state

- Upstream: `Stellar-kraal/stellar-kraal-contract`
- Default branch: `main`
- Main SHA: `e57cc72c85c58b544af2fd698f569d8a149dd917`
- Issue: `#109 [Backend] Add Unit Tests for stableStringify and Idempotency Fingerprint Stability`
- GitHub: OPEN, no assignee, 2 issue comments, no matching PR found.
- GrantFox public page: `https://contribute.grantfox.xyz/org/Stellar-kraal/repo/stellar-kraal-contract/issue/109`
- Provider state at observation: **Unassigned**, Apply route visible, 1 application per user / direct GitHub comment.
- Labels include `testing`, `backend`, `security`, `Maybe Rewarded`, `GrantFox OSS`, `Third Campaign`. These labels do not prove an award or payout.

The two existing issue comments are application expressions, not implementation carriers. One is generic; the other proposes Soroban/auth/storage work that does not match this TypeScript-only issue. Treat them as application pressure, not evidence the requested test file exists.

## Exact source pins

| Surface | Blob SHA | Relevant behavior |
|---|---|---|
| `backend/src/middleware/idempotency.ts` | `7404e545c38facc5770bb616cbffde423a4d4bcd` | Exports `stableStringify`; recursively sorts object keys; preserves array order; uses JSON encoding for keys/primitives. Private `fingerprintOf` hashes method + route + stable body. |
| `backend/tests/idempotency.integration.test.ts` | `a53970862d343e152e17822dad11deb3c752b299` | Broad endpoint/replay/reconciliation/TTL coverage, but no dedicated serializer unit suite. |
| `backend/package.json` | `5cc8e52f55750754178238cf265bbe451daaf3b6` | `npm test` = `jest --runInBand`; Jest + ts-jest present; Node >=20. |
| `backend/tsconfig.json` | `43fcea1e81ea29e39fc3f6c9806bf52468ab6731` | Strict TS; production compiler includes `src` only, so tests are driven by ts-jest rather than the production build root. |
| `backend/jest.config.js` | current main | `preset: 'ts-jest'`, Node environment, tests rooted at `backend/tests`. |

## Current serializer / fingerprint contract

`stableStringify` is deliberately small:

- arrays recurse element-by-element **without sorting**, so array order is semantic;
- non-null objects use `Object.entries(...).sort` by raw JS string comparison of keys, then recurse through values;
- keys use `JSON.stringify(k)`, preserving JSON escaping;
- primitives flow through `JSON.stringify(value)`.

The request fingerprint is SHA-256 of:

```text
<METHOD> <baseUrl><path>
<stableStringify(body ?? {})>
```

The fingerprint helper is private. The issue explicitly forbids production-code changes, so the test should not widen the production API just to expose `fingerprintOf`.

## Assignment-ready unit matrix

Target file: `backend/tests/idempotency.unit.test.ts`.

Minimum eight is required; use at least these twelve to cover the real trust boundary:

1. **Top-level sorting** — `{z:1,a:2}` canonicalizes to keys in `a,z` order.
2. **Nested sorting** — independently canonicalize key order at two or more object depths.
3. **Array preservation** — `[1,2,3]` differs from `[3,2,1]`; never sort arrays.
4. **Objects inside arrays** — object keys canonicalize while array slot order stays fixed.
5. **Arrays inside objects** — parent keys sort but nested array sequence remains unchanged.
6. **Unicode values** — accented text, CJK, and emoji survive canonicalization exactly according to JSON encoding.
7. **Escaping** — quote/backslash/newline-containing keys or string values remain valid JSON and deterministic.
8. **Insertion-order equivalence** — two deeply equal bodies created with different object insertion orders produce exactly the same stable string.
9. **Fingerprint equivalence** — for one fixed method + path, the two insertion-order variants produce the same SHA-256 fingerprint.
10. **Changed scalar fingerprint** — alter one top-level value and prove the hash changes.
11. **Changed nested fingerprint** — alter one nested value and prove the hash changes.
12. **Primitive/null control** — deterministic `null`, boolean, number, and string serialization.

Useful extra hostile case: numeric-looking object keys. JavaScript object enumeration has special rules for integer-index keys before `Object.entries`, but the implementation explicitly sorts the resulting string keys. Pin the actual expected lexical comparison behavior rather than assuming locale or numeric ordering.

## Fingerprint test without production API widening

Keep production code unchanged. In the unit test, define a tiny test-only helper that mirrors the documented preimage construction while calling the real exported `stableStringify`:

```ts
function testFingerprint(method: string, route: string, body: unknown): string {
  return createHash('sha256')
    .update(`${method} ${route}\n${stableStringify(body ?? {})}`)
    .digest('hex');
}
```

This proves the acceptance property that body insertion-order differences do not perturb the canonical preimage/hash, without exporting the private Express-specific helper solely for test visibility. The existing integration suite already exercises the real middleware fingerprint path end-to-end.

## Boundaries that should stay explicit

- Scope is JSON request bodies. Do **not** claim canonical behavior for arbitrary JavaScript values such as functions, symbols, or `undefined`; those are outside the Express JSON-body contract and have JSON-specific edge behavior.
- Do not sort arrays: doing so would make semantically different financial payloads collide.
- Do not make locale-sensitive key sorting part of the test contract. Production uses raw `<` / `>` string comparison.
- No production source change is needed for the stated issue.
- Avoid replacing the existing integration suite; this file is a narrow regression layer around canonicalization/fingerprint stability.

## Validation contract

After assignment and immediately after re-pinning current main:

```bash
cd backend
npm test -- --runTestsByPath tests/idempotency.unit.test.ts
npm test
npm run lint
```

Acceptance evidence should include:
- >=8 focused tests (12 above recommended);
- reordered object keys -> identical stable string and fingerprint;
- any tested value change -> different fingerprint;
- array-order sensitivity preserved;
- full backend Jest suite passes;
- no production files changed.

## Custody / reward fence

This baseline is source/readiness evidence only. It does not claim GrantFox assignment, award, payment, or payout. Upstream implementation should begin only after the live provider/maintainer assignment requirement is satisfied and issue/PR/source state is refreshed.
