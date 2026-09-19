# Gryd-lock #44 — synthetic/offline fixture contract baseline

Source-readiness packet for `Gryd-lock/grydlock-testkit#44`, pinned to
`main@7064404d6e7c44df1f980d532d23901651d79426`.

Current source already makes the core architecture decision:
- `destinations.json` blob `c8f918089f700982d7347cc1d8af8d8677fd5774`: 12 rows; every row says `fixture_status: synthetic-only`.
- `scripts/validate-fixtures.mjs` blob `90ea1abb6136d939aa4d1e0ac422b23f74fa6362`: only `synthetic-only` is accepted.
- README blob `f8fd8bdb89cca82a0ef42735e3daf3b1cacfa895`: StubOracle, replay and evaluation are offline.
- CI blob `53ce3ea3359798fd8b34d3a06728ae8110f7277c`: deterministic local validation; no Horizon dependency.

Residual work is therefore explicit documentation plus a read-only collision audit, not a live-fixture conversion.

## Assignment-time contract

1. Build the unique Stellar address set from account rows plus asset issuers.
2. Query canonical Stellar testnet Horizon account lookups with bounded timeout/concurrency.
3. HTTP 404 => expected synthetic-only evidence.
4. HTTP 200 => collision found; return nonzero.
5. Timeout, 429, 5xx, malformed response, unexpected status, or transport failure => UNKNOWN/nonzero.
6. Emit a deterministic summary bound to source revision, endpoint, address count and per-address result.

The issue permits a clearly labelled manual script. Keep ordinary push/PR CI offline and deterministic;
prefer `scripts/check-testnet-collisions.mjs` + `npm run check:testnet-collisions`, optionally wrapped
by `workflow_dispatch`, rather than making every PR depend on live testnet availability.

Required regressions: all 404 => pass; one 200 => collision; duplicate issuer/address deduplicated;
429/5xx/timeout/malformed response => unknown failure; wrong network cannot yield a testnet pass;
existing fixture-status validation remains unchanged.

Any future live fixture subset belongs in a separate design issue. Implementation remains assignment-gated.
