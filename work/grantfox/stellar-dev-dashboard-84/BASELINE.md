# GrantFox baseline — Stellar-devs-dashboard/Stellar-dev-dashboard #84

Operation: `GFOX2-20260919-076/R-registry-promotion-architecture`  
Worker: ZZ-Sol-Forge · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream: `Stellar-devs-dashboard/Stellar-dev-dashboard`  
Pinned upstream branch: `master`  
Pinned upstream head: `6f02134e3fa89f8adde208af8eef99017f5f1bea`  
Issue: https://github.com/Stellar-devs-dashboard/Stellar-dev-dashboard/issues/84

## Authority fence

At observation time issue #84 is OPEN and has no GitHub assignee. The connected GitHub installation exposes upstream as pull-only (`pull=true`, `push=false`). The exact GrantFox Slack work ID had no competing TAKE/PROGRESS/DONE when this lane was claimed.

This package is a pre-assignment source/architecture audit. It does not:
- submit a provider application;
- claim provider assignment, eligibility, reward, or payment;
- mutate upstream source;
- execute a live Stellar/Soroban transaction;
- treat local simulations as on-chain evidence.

## Issue bar

#84 is intentionally a large feature track. It requires a versioned Soroban deployment registry and environment-promotion dashboard with:
- IndexedDB persistence, migration, validation, atomic updates, import/export;
- durable deployment provenance;
- named local/test/staging/production environments;
- configurable promotion gates;
- executable and instance-data drift detection;
- external-deployment reconciliation;
- registry/diff/promotion/provenance/status UI;
- redaction and recovery;
- unit/integration/Playwright/visual/accessibility coverage;
- at least 1,000 lines of meaningful production implementation.

The current repository already contains useful deployment, RPC, compatibility, storage, audit, accessibility, test, and UI infrastructure. The safe implementation should compose those seams instead of creating a disconnected mini-application.

## Critical finding 1 — current deployment receipts are not authoritative chain evidence

Pinned source:
- `src/lib/deployment/ContractDeployer.ts` — blob `baea13f1daaf0d2a0065b7b051b59fbe0955c7c3`
- `src/components/deployment/ContractDeployer.tsx` — blob `90a2e0737aea5faae06367b75a7eec23311b0871`
- `src/components/deployment/DeploymentTracker.tsx` — blob `ca81f318abd854d8d8ba92688dca0c6372e0b688`

The library's `deployContract()`:
1. hashes WASM bytes;
2. generates a contract id locally from the WASM hash;
3. generates a transaction hash locally from WASM hash + source account + network + current time;
4. constructs a receipt;
5. returns `submitted` for testnet without a Soroban RPC submission in this class;
6. keeps mainnet simulation-only.

The UI then adds a short artificial delay and updates the receipt timeline with a "Network confirmed" entry.

This is useful demo/development behavior, but it is not sufficient evidence for a production deployment registry.

### Required architectural consequence

Every registry deployment version needs an explicit evidence class. At minimum:

- `confirmed-chain` — independently read back from the intended network and tied to real chain evidence;
- `external-observed` — contract exists on chain but historical deployer/constructor/tx provenance is incomplete;
- `simulation` — simulated only;
- `legacy-synthetic` — current/demo receipt format or imported synthetic evidence;
- `unverified` — captured but not yet independently verified.

Promotion must fail closed unless the source deployment satisfies the configured evidence requirement. A receipt-shaped object must never imply `confirmed-chain` merely because its status field says "confirmed".

## Critical finding 2 — WASM hashing has a non-cryptographic fallback

Pinned:
`src/lib/deployment/WASMProcessor.ts@499b6db1d36b6f881c8ff6e82d8cc622abaca98f`

`hashBytes()` uses Web Crypto SHA-256 when available, but falls back to a small FNV-like 32-bit value when Web Crypto is unavailable or rejects the input realm.

That fallback is acceptable for deterministic test/demo identity, but not for:
- artifact provenance;
- duplicate deployment identity;
- promotion gates;
- executable drift comparison;
- signed/audited exports.

### Required consequence

Registry artifact identity must explicitly require a cryptographic digest, preferably:
- `algorithm: "sha-256"`;
- 32-byte / 64-hex digest;
- hard error or `unverifiable` evidence if SHA-256 cannot be obtained.

Do not silently store the current fallback as a trusted `wasmSha256`.

## Persistence seams

Pinned:
`src/lib/storage.ts@ae519a00772ebe8bfc02eae851619385ca649fb3`

Current storage uses native IndexedDB:
- database `stellar-dev-dashboard`;
- DB version 3;
- stores for app state, API cache, offline queue, and contract interaction history;
- `onversionchange` closes the open handle;
- a generic transaction helper.

However, generic app-state reads/writes silently fall back to `localStorage` when IndexedDB fails.

That fallback is dangerous for #84 because a deployment registry needs:
- multi-record atomic writes;
- migration guarantees;
- uniqueness constraints;
- revision conflict detection;
- explicit storage failure/degraded state.

A registry mutation must not silently downgrade from an IndexedDB transaction to independent localStorage writes.

The package already includes:
- `idb@^8.0.3`;
- `fake-indexeddb@^6.2.5`.

A dedicated deployment-registry database/repository boundary is therefore a strong fit and isolates schema migration risk from generic app storage.

## Cross-tab seam

Pinned:
`src/utils/stateSync.ts@225c213d48772369780793dc3a6934e889496419`

The app already uses `BroadcastChannel` for state/storage synchronization. This can be reused as a notification transport, but registry correctness should not depend on last-message-wins payload replication.

Recommended pattern:
- mutations commit in IndexedDB with an expected entity revision / registry revision;
- after commit, broadcast only an invalidation/version message;
- other tabs refetch;
- stale writers receive a typed conflict and must review/retry.

## Network and RPC seams

Pinned:
`src/lib/stellar.ts@564f26c224e0219b93aabd6a4f6614b0b661dc99`

Existing `NetworkName` supports:
- mainnet;
- testnet;
- futurenet;
- local;
- custom.

`NetworkConfig` includes the network passphrase and Soroban/Horizon endpoints. The app already exposes `getSorobanServer(network)`, contract reads, simulations, transaction submission support elsewhere in the file, and custom network profiles.

Custom auth headers are kept in session storage. They must never be copied into deployment manifests or exports.

The deployment environment identity should be bound primarily to network passphrase, not only the friendly network enum/name. Two endpoints with the same friendly label but different passphrase must not be conflated.

## On-chain drift seam

Pinned:
`src/lib/contractStorage.ts@618d21e926b91cb8b3aea282196b763806a1a6f6`

The existing storage inspector already:
- constructs the contract-instance ledger key;
- reads instance storage via Soroban RPC;
- reports latest ledger;
- tracks last-modified/TTL metadata;
- reads persistent/temporary keys discovered from contract interaction history.

This is useful for instance-data drift.

Executable drift needs a dedicated chain reader:
1. read the contract instance;
2. resolve the `ContractExecutable`;
3. if executable is WASM, extract the on-chain WASM hash;
4. compare that cryptographic hash with the registry's verified artifact hash;
5. capture the observed ledger and observation timestamp.

Do not compare pretty-printed JSON for authoritative identity when canonical XDR bytes are available.

## Compatibility subsystem is a strong implementation pattern

Pinned:
- `src/types/compatibility.ts@1c45309df67a5b65f393ab04c02c0ab716622df3`
- `src/lib/compatibility/persistence.ts@affd52c7db49dd2b5c2ab12ec39b4834451bbdc4`
- `src/lib/compatibility/redaction.ts@27fbd991a7715ee7f5faa44b122ebd55f7a0076d`
- `src/lib/compatibility/audit.ts@937559fd46c32ba81c9e7e4f6ff2fcc7ec6c761f`
- `src/hooks/useCompatibility.ts@eced6155b5abcf86ca5411ab06f12218836a2a41`

Useful established patterns:
- explicit schema version constants;
- typed degraded/offline/unknown states;
- network identity and evidence;
- bounded timeouts and AbortController cancellation;
- versioned/validated imports;
- import-size limits;
- redacted exports;
- expiring attributed overrides;
- upgrade-readiness audit artifacts;
- clean service ↔ hook ↔ UI separation.

The registry should align with these conventions where practical.

## Do not reuse DevOps canary scoring as Soroban promotion authority

Pinned:
`services/devops-automation/lib/deploymentSafety.mjs@41e393ce56b69b7c84a6d617ce4b6df05cfbcee6`

This service contains code-deployment risk scoring, canary traffic shifting, rollback recommendations, and Kubernetes/configuration drift concepts.

Those are useful vocabulary/examples for UI state, but they are not the same problem as a Soroban deployment promotion gate. A registry promotion decision should be deterministic from:
- verified artifact identity;
- intended network identity;
- on-chain evidence;
- drift status;
- configured environment gate policy;
- explicit user/maintainer approval where required.

Do not let an ML-ish code risk score override missing chain evidence.

## Audit trail seam and limitation

Pinned:
`src/lib/auditTrail.ts@c712b29c2da88a36ac546be72ceb7cdaca2985ac`

The global audit system:
- sanitizes/redacts data;
- supports event export;
- publishes to subscribers;
- persists a bounded recent event set to localStorage.

But it stores only the newest 1,000 events and can be cleared.

Therefore:
- mirror sanitized registry events into the global audit trail for app observability;
- keep the registry's own mutation/promotion/reconciliation event records inside the registry transaction;
- do not rely on global auditTrail as the durable source of registry history.

A browser-only audit can be made tamper-evident with hash chaining, but it must not be described as externally immutable or non-repudiable.

## UI integration seam

Pinned:
- `src/App.tsx@1afa6c7e6e717e4068ab964857c8796099e8e8e0`
- `src/components/layout/Sidebar.tsx@84b0eb49dabebfeb4fc263f9d46fb8178371ccf0`
- `src/components/dashboard/Contracts.tsx@d379b14790d41a6393334ee0f00069c9f220e01b`

The app is tab/route driven. `TABS` in `App.tsx` lazily loads major dashboards; Sidebar defines matching navigation entries.

Contract deployment currently lives inside the Contracts dashboard's `deploy` mode rather than as an independent registry route.

A cohesive #84 implementation can:
- add a lazy `deploymentRegistry` tab/dashboard;
- add one sidebar item near Contracts/Compatibility;
- allow current deployment UI to hand a receipt to the registry capture/verification service;
- link from a registry row back to Contracts/Contract Storage/Compatibility context.

Do not bury the entire registry inside the existing multi-mode Contracts component.

## Security / privacy boundary

Issue #84 explicitly requires sensitive-value redaction from UI errors, logs, tests, screenshots, browser storage, and exports by default.

Existing redaction helpers already cover:
- bearer-like credentials;
- Stellar secret keys;
- sensitive key names;
- URL query/hash redaction.

Deployment registry rules should additionally enforce:
- secret keys/private signing material are rejected from manifest fields;
- custom RPC auth headers are never persisted/exported;
- constructor argument values that are classified sensitive are stored as redacted descriptors plus cryptographic value digest by default, not raw secret text;
- imported documents are scanned/rejected or redacted before persistence;
- errors never include raw signed XDR/auth material unless explicitly safe and bounded.

## Source pins

| Surface | Blob |
| --- | --- |
| package | `a8b86f76515fed3e3c38ed05f804f44dc7eca312` |
| App | `1afa6c7e6e717e4068ab964857c8796099e8e8e0` |
| Sidebar | `84b0eb49dabebfeb4fc263f9d46fb8178371ccf0` |
| Contracts dashboard | `d379b14790d41a6393334ee0f00069c9f220e01b` |
| deployment library | `baea13f1daaf0d2a0065b7b051b59fbe0955c7c3` |
| deploy UI | `90a2e0737aea5faae06367b75a7eec23311b0871` |
| tracker | `ca81f318abd854d8d8ba92688dca0c6372e0b688` |
| WASM processor | `499b6db1d36b6f881c8ff6e82d8cc622abaca98f` |
| storage | `ae519a00772ebe8bfc02eae851619385ca649fb3` |
| cross-tab sync | `225c213d48772369780793dc3a6934e889496419` |
| Stellar/RPC | `564f26c224e0219b93aabd6a4f6614b0b661dc99` |
| contract storage | `618d21e926b91cb8b3aea282196b763806a1a6f6` |
| global audit | `c712b29c2da88a36ac546be72ceb7cdaca2985ac` |
| compatibility types | `1c45309df67a5b65f393ab04c02c0ab716622df3` |
| compatibility persistence | `affd52c7db49dd2b5c2ab12ec39b4834451bbdc4` |
| compatibility redaction | `27fbd991a7715ee7f5faa44b122ebd55f7a0076d` |
| compatibility audit | `937559fd46c32ba81c9e7e4f6ff2fcc7ec6c761f` |
| devops deployment safety | `41e393ce56b69b7c84a6d617ce4b6df05cfbcee6` |

## Disposition

**READY_FOR_ASSIGNMENT_WITH_EVIDENCE-CLASS REPAIR AS A FIRST-CLASS DEPENDENCY.**

A safe #84 implementation should not begin by building a glossy registry around current receipts. It should first define the evidence model and authoritative verification boundary, then persist/version/present that evidence. The concrete architecture, state machines, schema, migration plan, drift semantics, stale-tab controls, redaction rules, test matrix, and 1,000+ production-line decomposition are in `ARCHITECTURE.md`.
