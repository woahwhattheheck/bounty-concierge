# Stellar-Analysis/frontend — GrantFox backend-drift census

## Executive disposition

**SUPPLY HYGIENE: do not treat open `backend/**` issues in this repository as fresh greenfield implementation lanes until the maintainer names the canonical backend target.**

Canonical upstream `main@482ee456369418ef82c4056718cb82d3468f762b` is the Next.js frontend tree and no longer contains the Rust backend assumed by this issue family. This is not a claim that the issues are invalid forever; it is a source-lineage fence against implementing them on the wrong branch or resurrecting removed code without maintainer authority.

This census grants no GrantFox application, assignment, reward, payment, or upstream-write authority.

## Canonical evidence

Current `main`:

- commit: `482ee456369418ef82c4056718cb82d3468f762b`
- `README.md` blob: `a7546f7a6f85709fccd21e136cd8b12bae820652` — identifies this repo as the Next.js/React frontend.
- `package.json` blob: `2b1c6ac1f83096666c7fd6d5ba3fd22780e6b8eb` — frontend dependencies/scripts; no Redis client.
- `backend/Cargo.toml`: absent on current `main`.
- `backend/src/lib.rs`: absent on current `main`.
- root `Cargo.toml`: absent on current `main`.

Historical backend ancestor:

- `d8bae439cbaeb0f6b3a4fb36ffe212993c3af617`
- historical `backend/Cargo.toml` blob: `5f15f0b4c473a688ff5a42767c4ad9abdb477b8d`
- current main is 41 commits ahead of that ancestor.
- GitHub comparison from the ancestor to current main reports `backend/**` removed, including Cargo manifests, distributed-lock, network, realtime, reconciliation, replay, snapshot, contract-ops, and backend tests.

That history matters: the issue family was written against a real prior backend, but canonical main subsequently removed it.

## Open backend-dependent GrantFox issues

The current open `GrantFox OSS` issue census contains **20 issues whose bodies explicitly target `backend/**`**:

| Issue | Scope | Current routing |
|---|---|---|
| #319 | exactly-once ledger ingestion | **existing upstream PR #392**; review carrier, not fresh lane |
| #321 | unified cache/invalidation | **HOLD canonical backend target** |
| #323 | muxed-account rate limiting | **HOLD canonical backend target** |
| #324 | GraphQL+dataloader+cost limits | **HOLD canonical backend target** |
| #327 | Vault/live secret rotation | **HOLD canonical backend target** |
| #328 | API deprecation evidence | **HOLD canonical backend target** |
| #329 | signed requests + nonce replay | internal source-fence landed in bounty-concierge #387; **HOLD canonical backend target** |
| #330 | collision-proof cursor pagination | **HOLD canonical backend target** |
| #331 | anomaly detection feedback loop | **HOLD canonical backend target** |
| #332 | tamper-evident audit log | **HOLD canonical backend target** |
| #333 | point-in-time backup/restore | **HOLD canonical backend target** |
| #334 | reverse-proxy-safe IP allowlisting | **HOLD canonical backend target** |
| #335 | idempotent/dead-letter job runner | **HOLD canonical backend target** |
| #336 | signed/idempotent webhooks | **HOLD canonical backend target** |
| #337 | sparse field selection | **existing upstream PR #391**; review carrier, not fresh lane |
| #378 | distributed LockStore/fencing | **existing upstream PR #386**; review carrier, not fresh lane |
| #379 | realtime fanout isolation | **existing upstream PR #393**; review carrier, not fresh lane |
| #380 | reconciliation fault tolerance | **existing upstream PR #394**; review carrier, not fresh lane |
| #382 | TTL budget/priority scheduling | **existing upstream PR #384**; review carrier, not fresh lane |
| #383 | payment reliability/percentiles | **existing upstream PR #387**; review carrier, not fresh lane |

The upstream PR census is live GitHub state from this review. It should be refreshed before acting because these carriers may merge, close, or be superseded.

## Contract-only issues are NOT blocked by this backend fence

Three open GrantFox issues in the same repository target `contracts/**`, not the removed backend:

- #342 — governance quorum
- #346 — access-control / live revocation
- #381 — upgrade privilege-scope hardening (currently has upstream PR #389)

Do not suppress those merely because the backend disappeared.

## Why existing backend PRs need review, not automatic rejection

Several current open upstream PRs still carry `backend/**` implementations. At least PR #386 reports base `d8bae439...`, the historical backend ancestor. Its head contains a Rust backend and Redis dependency, while canonical main no longer does.

Therefore there are two distinct states:

1. **No implementation carrier:** do not create a fresh Rust backend from current main. Ask the maintainer which repository/branch restores or replaces the backend.
2. **Existing implementation carrier:** review that carrier's topology and maintainer intent. It may be an intentional backend restoration line, or it may be stale against the removed tree. Do not spawn a duplicate.

## Maintainer clarification packet

Before any new backend issue is assigned for implementation, get explicit answers to:

1. Is the Rust backend intentionally removed from `Stellar-Analysis/frontend/main`, relocated to another repository, or expected to be restored?
2. What exact repository + branch/commit is canonical for backend bounties?
3. Are current backend PRs (#384/#386/#387/#391/#392/#393/#394) expected to reintroduce/restore that tree, or should they retarget elsewhere?
4. Which backend PR, if any, is the prerequisite substrate for currently uncarried issues #321/#323/#324/#327–#336?
5. Should GrantFox issue text be updated so future applicants do not branch from frontend-only main and recreate a backend independently?

## Swarm routing rule

Until that answer exists:

- **existing upstream carrier** → review/finalize that exact carrier; do not duplicate;
- **no upstream carrier + `backend/**` prescription** → `HOLD_CANONICAL_BACKEND_TARGET`;
- **`contracts/**` issue** → evaluate normally on current contract source;
- always refresh issue/PR/provider state immediately before application or implementation.

This map is intended to prevent an entire class of expensive wrong-base work, not to reserve implementation custody.
