# GrantFox baseline — Gryd-lock/grydlock-testkit #11

Operation: `GFOX3-20260919-gryd-lock-grydlock-testkit-11-R`
Worker: ZZ-Sol-Delta · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream head: `7064404d6e7c44df1f980d532d23901651d79426`

## Canonical issue and provider state

- GitHub: https://github.com/Gryd-lock/grydlock-testkit/issues/11
- GrantFox: https://contribute.grantfox.xyz/org/Gryd-lock/repo/grydlock-testkit/issue/11
- Observed issue/provider state: OPEN / GrantFox Unassigned
- GrantFox shows Apply enabled, one application per user, direct GitHub-comment application, and 1 existing applicant comment.
- Public GitHub issue page showed no Development branch or linked pull request at the observation.
- Labels include `Maybe Rewarded`, `GrantFox OSS`, and `Official Campaign | FWC26`.
- Reward boundary: eligibility labels do not establish an award or payment.

This packet is pre-assignment source evidence only. It does not grant implementation authority.

## Current-main drift correction

The issue's README restructuring request is stale at the pinned head. Current `transactions/README.md` already uses a transaction-level envelope section plus an ordered operation table that explicitly supports arbitrary multi-operation fixtures. Current `transactions/index.json` also records machine-readable envelope metadata and per-operation details.

The actual requested fixture gap remains live: the tree contains `payment.xdr`, `path_payment.xdr`, `change_trust.xdr`, and `fee_bump_payment.xdr`; it does not contain `change_trust_then_payment.xdr` or `multi_payment.xdr`.

Pinned evidence:

- `transactions/README.md`: `e052e5a5d4f5ef8934dc74df7199b28c89987df1`
- `transactions/index.json`: `a07a007e9a9e8f418d918044578a792014472a2b`
- `destinations.json`: `c8f918089f700982d7347cc1d8af8d8677fd5774`
- `scripts/validate-fixtures.mjs`: `90ea1abb6136d939aa4d1e0ac422b23f74fa6362`
- `package.json`: `7d674c82cfca132d720a02dbc56d45df3979e950`

## Existing fixture contract

`transactions/index.json` pins:

- network `TESTNET`
- passphrase `Test SDF Network ; September 2015`
- unsigned envelopes
- per-fixture TESTNET transaction hash, source, description, envelope type, and ordered operations.

Current reusable identities include:

- clean source: `GCRRYBV5IY7DSI54DKW33ZELC2LWYCAHC43TXAM2A2HTFN5GWOFWXPC2`
- suspicious/pass-through destination: `GCRNKXJJLZNDLK2EWPX25JISTORCXCF2HYUXMYKF7XWKHMEOHCXVGP4J`
- malicious sweep destination: `GD7XPB2A7CG5Z4ICV24B3LXCRHAEJRFEK4OEW3ZIOQAPJOHAXBB7QHGE`
- malicious rug-pull destination: `GDQPZVGOJY6Q4PPASHZBIFN3PTIBD6WCRDCCAIFIPHZIYKSDQ7PZWPNJ`
- SCAM issuer: `GAJLLIIPHII6OCG4KQJIGPCHVN6DNCRBXHX6DEUTPE7MQ6OONAYBRLET`
- SCAM asset identity: `SCAM:GAJLLIIPHII6OCG4KQJIGPCHVN6DNCRBXHX6DEUTPE7MQ6OONAYBRLET`

Every address/asset used by a new fixture should resolve against `destinations.json`; do not mint new fixture identities unnecessarily.

## Residual implementation after assignment

1. Generate `change_trust_then_payment.xdr` offline with `TransactionBuilder` and `Networks.TESTNET`: a SCAM `changeTrust` immediately followed by a payment, using only declared synthetic fixture identities.
2. Generate `multi_payment.xdr` with at least two payment operations to distinct declared fixture destinations.
3. Extend `transactions/index.json` with exact envelope type, unsigned state, TESTNET hash, source, description, and ordered operation metadata for both fixtures.
4. Add corresponding README envelope/operation sections using the already-landed multi-operation format rather than restructuring the document again.
5. Add decode assertions that load every new XDR through `TransactionBuilder.fromXDR(xdr, Networks.TESTNET)` and prove operation count/order/type plus source/destination/asset semantics.
6. Run `npm test` and `npm run validate`. Preserve existing referential-integrity and synthetic-only boundaries.

The current `scripts/` tree contains validation/replay utilities but no XDR generation script. An assigned implementation may add a deterministic fixture generator if that matches maintainer preference, but #11 only requires committed decodeable fixtures plus accurate documentation/index evidence.

## Application packet

A strong application should explicitly acknowledge that README/index portability work already landed and scope itself to the two missing multi-operation envelopes plus deterministic decode/index validation. It should not claim the repository still has a one-row-per-file README.

No provider application was submitted by this worker. No upstream source was mutated.
