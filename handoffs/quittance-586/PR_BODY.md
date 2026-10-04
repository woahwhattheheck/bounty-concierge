## Why

A public pay link exposes the seller address, so using that address as a workspace credential allowed any link holder to request private customer fields and mint invoices under another wallet. This change makes seller access depend on proof of wallet control and retires the indefinitely reusable cancellation signature.

Closes #586.

## What changes

- Add server-issued SEP-10 challenge and session routes using the existing Stellar SDK. Challenges bind the configured home/API domains, account and pinned network, expire within five minutes and can be redeemed once.
- Issue HMAC-signed sessions with an environment-selected key ID and a maximum one-hour lifetime. Configuration fails closed and supports overlapping verification keys during rotation.
- Enforce verified seller identity in the shared invoice router used by all three server modes. List, stats, events, create and cancel require a session; only an owning session receives private invoice fields. A declared seller key that differs from the session returns 403.
- Use Freighter `signTransaction` for authentication. The browser retains tokens only in memory, invalidates them synchronously on account/network/disconnect changes, aborts stale seller requests and retries one 401 within the original wallet session. Public checkout remains anonymous.
- Mirror the challenge/session contract in the mock API, allow the Authorization CORS header and migrate existing HTTP fixtures, cancellation callers and smoke scripts to the same contract.

The additional changed files are existing test fixtures and script callers that previously used an unsigned seller key. No dependency, account-table or database-schema change is required.

## Acceptance coverage

- The named pay-link regression obtains the seller key from the public DTO, repeats the request without a session, and asserts that `customerEmail` is absent.
- Mounted routes cover anonymous 401 responses, body/query/header identity mismatches, cross-seller access, authenticated creation and cancellation, and the configured-origin Authorization preflight.
- Real SDK challenges cover invalid signatures, replay, strict expiry, network/domain mismatch, bounded nonce admission and HMAC key rotation.
- Frontend tests exercise real Axios interception, the Freighter signing boundary, account switches during authentication/fetch, memory-only tokens, cancellation through a session and a single 401 refresh.

## Validation

[Clean cloud run 37204495861](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37204495861) checked out immutable source `d9322f3f5a11533d46825c2894034e10214a3192` on Ubuntu 24.04, Node 24.21.0 and npm 11.19.0, using the repository's existing commands and unchanged lockfiles:

| Check | Result |
| --- | --- |
| Backend `npm ci`, `npm run typecheck`, `npm test` | **911 passed**, 0 failed/cancelled/skipped |
| Frontend `npm ci`, `npm run lint`, `npm run typecheck`, `npm test` | **770 passed**, 0 failed/cancelled/skipped; lint retains its three existing warnings |
| Root `node --test "tests/**/*.test.mjs"` | **41 passed**, 0 failed/cancelled/skipped |

The run publishes backend, frontend and shared-test logs as seven-day artifacts. It requires no seller/payer secrets; the optional live Testnet evidence step remains separately secrets-gated.

## Deployment notes

Configure the dedicated challenge signing key, home/API domains and HMAC key ring described in [docs/SELLER_AUTH.md](docs/SELLER_AUTH.md). Existing seller clients must authenticate; there is no development or legacy-cancel bypass.

Challenge nonces are bounded and process-local. Issuance and redemption must reach the same process for the five-minute exchange; a restart or another process rejects an unknown challenge and requires a fresh one. Session verification is stateless across processes sharing the same domain, network and key ring. Cross-process challenge redemption would require shared atomic nonce storage.
