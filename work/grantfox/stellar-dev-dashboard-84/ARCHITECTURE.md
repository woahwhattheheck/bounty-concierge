# Architecture — versioned Soroban deployment registry and promotion dashboard

Target: `Stellar-devs-dashboard/Stellar-dev-dashboard#84`  
Pinned source: `master@6f02134e3fa89f8adde208af8eef99017f5f1bea`

## 1. Core safety invariant

A deployment registry is only as trustworthy as its evidence.

The existing demo deployment receipt must be treated as a **claim**, not as proof. The registry must independently represent:

1. what the user intended to deploy;
2. what a local tool reported;
3. what the target network can be observed to contain;
4. whether those observations are fresh enough for a promotion decision.

No UI status string, local receipt, import, or historical audit entry can upgrade evidence to `confirmed-chain` without an authoritative network observation.

## 2. Domain model

Use a dedicated versioned domain under a cohesive boundary such as:

```
src/types/deploymentRegistry.ts
src/lib/deploymentRegistry/
src/hooks/useDeploymentRegistry.ts
src/components/deployment-registry/
```

### 2.1 Schema version

```ts
export const DEPLOYMENT_REGISTRY_SCHEMA_VERSION = 1 as const;
```

Persist both:
- browser database schema version;
- logical export/manifest schema version.

They evolve independently.

### 2.2 Network identity

Friendly labels are not identity.

```ts
interface RegistryNetworkIdentity {
  networkName: NetworkName | 'custom';
  passphrase: string;
  passphraseSha256: string;
  horizonUrl?: string; // sanitized
  sorobanRpcUrl: string; // sanitized
}
```

Rules:
- the passphrase is public network identity and may be stored;
- endpoint userinfo/query/hash/auth material is removed before persistence/export;
- custom headers are never part of the manifest;
- any network mismatch during verification is a hard gate failure.

### 2.3 Artifact identity

```ts
interface ArtifactIdentity {
  algorithm: 'sha-256';
  wasmSha256: string; // exactly 64 lowercase hex
  sourceSha256?: string;
  sourceRef?: string; // commit/tag/path, sanitized
  sizeBytes?: number;
}
```

Never put the current weak fallback digest in `wasmSha256`.

If cryptographic hashing is unavailable:
- capture may proceed as `unverified` if maintainers want degraded mode;
- verification/promotion cannot proceed;
- UI must say why.

### 2.4 Evidence class

```ts
type DeploymentEvidenceClass =
  | 'confirmed-chain'
  | 'external-observed'
  | 'unverified'
  | 'simulation'
  | 'legacy-synthetic';
```

Semantics:

**confirmed-chain**
- intended network identity established;
- real contract id observed;
- executable hash independently read from target network;
- observed hash equals expected artifact SHA-256;
- observation ledger/time recorded;
- real tx hash is validated if the record claims one.

**external-observed**
- contract + executable are observable on chain;
- artifact identity can be verified;
- some creation provenance (constructor args, deployer, creation tx) is unknown.

**unverified**
- plausible record awaiting independent network readback.

**simulation**
- simulation only; never promotable as a deployed environment.

**legacy-synthetic**
- current/demo receipts with locally derived ids/hashes or imports without authoritative proof.

### 2.5 Contract track vs deployment version

Do not use only `contractId` as a primary key because the same encoded id may be presented against different network identities, and one contract can be upgraded over time.

Model:

```ts
interface ContractTrack {
  id: string;
  networkIdentityKey: string; // sha256(passphrase) + contractId
  contractId: string;
  environmentId: string;
  createdAt: string;
  updatedAt: string;
  revision: number;
  currentDeploymentVersionId: string | null;
}

interface DeploymentVersion {
  id: string;
  trackId: string;
  sequence: number;
  evidenceClass: DeploymentEvidenceClass;
  artifact: ArtifactIdentity;
  constructorArgs: StoredConstructorArg[];
  deployerAddress?: string;
  transactionHash?: string;
  ledgerSequence?: number;
  capturedAt: string;
  verifiedAt?: string;
  verification?: DeploymentVerification;
  provenance: DeploymentProvenance;
  revision: number;
}
```

A contract upgrade becomes a new `DeploymentVersion`, not destructive replacement of history.

### 2.6 Constructor arguments

The issue requires constructor inputs, while the security bar requires sensitive values redacted from browser storage by default.

Use:

```ts
interface StoredConstructorArg {
  name?: string;
  type: ConstructorArgType | string;
  storage: 'plain' | 'redacted';
  value?: string;
  redactedDisplay?: string;
  valueSha256?: string;
}
```

Rules:
- obvious secrets (Stellar secret key pattern, bearer/API tokens, sensitive key names) are rejected or stored as redacted+digest;
- the default import/export sanitizer never emits raw sensitive values;
- if a user explicitly opts into locally storing a non-obvious sensitive constructor value, that is a separate security feature requiring key management. Do not silently pretend localStorage/IndexedDB is encrypted.

### 2.7 Verification record

```ts
type VerificationStatus =
  | 'verified'
  | 'drifted'
  | 'unavailable'
  | 'network-mismatch'
  | 'not-found'
  | 'unsupported-executable'
  | 'error';

interface DeploymentVerification {
  status: VerificationStatus;
  observedAt: string;
  latestLedger: number | null;
  expectedPassphraseSha256: string;
  observedExecutable: {
    kind: 'wasm' | 'stellar-asset' | 'unknown';
    wasmSha256?: string;
  };
  expectedWasmSha256?: string;
  instanceDataSha256?: string;
  drift: DriftFinding[];
  endpoint: string; // sanitized
}
```

`unavailable` and `error` are not clean.

## 3. IndexedDB repository

### 3.1 Dedicated database

Recommended database:
`stellar-deployment-registry`

Reason:
- independent versioning/migration;
- no silent localStorage fallback;
- smaller blast radius;
- clear recovery/export story.

Stores:
- `meta`
- `environments`
- `contract-tracks`
- `deployment-versions`
- `promotion-plans`
- `audit-events`

Useful indexes:
- track by `networkIdentityKey`;
- track by `environmentId`;
- versions by `trackId,sequence`;
- versions by `artifact.wasmSha256`;
- plans by `sourceEnvironmentId,targetEnvironmentId,status`;
- audit by `sequence`, `entityId`, `timestamp`.

### 3.2 Logical revision

Maintain:
- `meta.registryRevision` monotonically increasing;
- per-entity `revision`.

Every write API takes an optional/required `expectedRevision` for edits.

Inside one readwrite transaction:
1. load current entity/revision;
2. reject stale expected revision;
3. validate mutation;
4. write entity;
5. append audit event;
6. increment registry revision;
7. commit;
8. only after commit, publish BroadcastChannel invalidation.

Never publish cross-tab state before the transaction completes.

### 3.3 Interrupted writes

IndexedDB transaction abort gives atomicity across object stores in the same DB.

Test:
- inject a failure after entity write but before audit/meta write;
- abort the transaction;
- assert entity, audit, and registryRevision are all unchanged.

Do not emulate atomicity with sequential `setStoredValue` calls.

### 3.4 Stale tabs

Use a registry-specific `BroadcastChannel`, e.g. `stellar-deployment-registry-v1`.

Messages:
```ts
{ type: 'REGISTRY_COMMITTED', registryRevision: 42 }
```

The message is an invalidation hint, not truth.

If local form opened at revision 7 and current persisted revision is 8:
- reject overwrite with `RegistryConflictError`;
- show current vs attempted change;
- let user reload/reapply.

No automatic last-writer-wins merge for promotion policy or verified deployment evidence.

## 4. Migrations

### 4.1 Migration contract

Each DB upgrade is explicit and testable:

```ts
type Migration = {
  from: number;
  to: number;
  migrate(db, tx): void | Promise<void>;
};
```

Browser IndexedDB upgrade transactions are atomic. If migration fails:
- open fails;
- no partial logical schema should be presented as usable;
- UI enters recovery/degraded state;
- offer safe export/backup where possible.

### 4.2 Logical document migration

Imported JSON is separate from DB migration.

Pipeline:
1. parse bounded bytes;
2. validate envelope/kind/version;
3. migrate supported older logical versions in memory;
4. revalidate final shape;
5. sanitize;
6. build conflict preview;
7. one atomic import transaction.

For future unsupported versions:
- do not coerce;
- report "newer schema not supported";
- leave current registry unchanged.

### 4.3 Legacy current receipts

Do not fabricate chain history.

Legacy/demo receipt import result:
```
evidenceClass = legacy-synthetic
verification = absent
promotion = blocked until independently reconciled
```

If chain reconciliation succeeds, add a new verification/evidence transition event; preserve original provenance.

## 5. Import/export

### 5.1 Export envelope

```ts
interface DeploymentRegistryExportV1 {
  kind: 'stellar-deployment-registry';
  schemaVersion: 1;
  generatedAt: string;
  registryRevision: number;
  redacted: true;
  environments: RegistryEnvironment[];
  contractTracks: ContractTrack[];
  deploymentVersions: DeploymentVersion[];
  promotionPlans: PromotionPlan[];
  auditEvents: RegistryAuditEvent[];
  digest: {
    algorithm: 'sha-256';
    value: string;
  };
}
```

Canonicalize before digest:
- deterministic key order;
- deterministic array sort by stable IDs/sequences;
- exclude the digest field itself.

Browser storage is user-controlled, so the digest detects accidental/tampering changes but is not a trusted digital signature.

### 5.2 Limits

Bound:
- raw import bytes;
- number of environments/tracks/versions/events;
- string lengths;
- constructor argument count/size;
- audit reason lengths.

Reject pathological imports before allocating huge derived structures.

### 5.3 Conflict preview

Classify:
- exact duplicate;
- same identity/equal content;
- same entity id/different content;
- same network+contract identity/different track id;
- unsupported/newer schema;
- invalid network identity;
- redaction violation.

Do not partially import a mixed-validity document unless a product decision explicitly introduces a reviewed partial-import mode.

## 6. Environment model

Named environments are not identical to Stellar network names.

```ts
type EnvironmentKind = 'local' | 'test' | 'staging' | 'production' | 'custom';

interface RegistryEnvironment {
  id: string;
  name: string;
  kind: EnvironmentKind;
  network: RegistryNetworkIdentity;
  promotionPolicy: PromotionPolicy;
  revision: number;
}
```

Examples:
- local-dev → local network;
- integration → testnet;
- staging → testnet or dedicated custom network;
- production → mainnet.

Do not force "staging" to mean a particular passphrase.

Environment comparison should compare:
- artifact versions;
- contract ids;
- executable hashes;
- last verified ledgers;
- drift status;
- evidence classes;
- verification age;
- constructor/provenance differences;
- compatibility/network readiness.

## 7. Promotion state machine

A promotion is a durable plan, not a button that immediately emits a transaction.

```ts
type PromotionStatus =
  | 'draft'
  | 'evaluating'
  | 'blocked'
  | 'ready'
  | 'awaiting-confirmation'
  | 'executing'
  | 'confirmed'
  | 'failed'
  | 'cancelled';
```

Allowed transitions must be explicit and unit-tested.

### 7.1 Deterministic gates

Suggested built-in gates:

**Source evidence**
- source evidence class meets policy;
- source verification is fresh;
- source executable drift is clean;
- source network identity matches environment.

**Artifact**
- cryptographic SHA-256 present;
- source artifact equals selected promotion artifact;
- compatibility audit passes if configured.

**Target**
- target network identity confirmed;
- RPC reachable;
- current target state reconciled;
- expected previous deployment version matches observed target state;
- no unresolved conflicting external deployment.

**Approval**
- explicit confirmation for production;
- optional required approver/reason;
- override must be attributed, reasoned, time-bounded, and policy-permitted.

Unknown/unavailable evidence is **block/review**, not allow.

### 7.2 Overrides

Reuse compatibility's good concepts, but promotion overrides must not be magical.

An override may waive only explicitly waivable policy gates.

Never allow override of:
- network identity mismatch;
- absent cryptographic artifact digest;
- inability to establish whether the target is the intended network;
- malformed registry/import data.

Override record:
- id;
- plan id;
- gate id;
- author;
- reason;
- createdAt;
- expiresAt;
- evidence refs;
- previous/resulting gate state.

## 8. Execution boundary

Because current deployment receipts are synthetic, #84 needs an explicit executor abstraction before it can honestly claim end-to-end promotion execution.

```ts
interface DeploymentExecutor {
  simulate(input, options): Promise<SimulationEvidence>;
  execute(input, options): Promise<SubmissionEvidence>;
  awaitConfirmation(input, options): Promise<ChainConfirmation>;
}
```

Production implementation choices:

**A. Registry-first, planning/reconciliation release**
- registry, drift, diffs, plans, gates, exports all production-ready;
- actual promotion execution remains disabled until an authoritative executor lands;
- UI says "Generate promotion plan" rather than "Promote".

**B. Deliver an authoritative executor inside #84**
- use existing Stellar SDK/network infrastructure;
- produce real send/confirmation evidence;
- independently read contract executable back after confirmation;
- capture real ledger/tx hash;
- never reuse the current synthetic ID/hash generator as proof.

If the bounty expects executable promotion, choose B after assignment. The registry architecture supports either path without lying about evidence.

## 9. On-chain drift

### 9.1 Executable drift

For a tracked contract:
1. validate contract id;
2. bind to environment passphrase/RPC;
3. request the contract instance ledger entry;
4. inspect `ContractExecutable`;
5. for WASM executable, read/normalize the 32-byte hash;
6. compare to expected `artifact.wasmSha256`;
7. record latest ledger and sanitized endpoint.

Statuses:
- clean;
- executable-drift;
- unsupported-executable;
- contract-not-found;
- network-mismatch;
- unavailable/error.

### 9.2 Instance-data drift

Define exactly what is being compared.

Do not hash a locale-sensitive pretty-printed JSON rendering.

Use canonical source bytes:
- XDR for the contract instance/storage map when available;
- deterministic ordered serialization for derived normalized values.

A baseline should record:
- canonical hash;
- observed ledger;
- included scope.

For persistent/temporary storage, the existing app only discovers keys from interaction history. UI must distinguish:
- full instance-state comparison;
- partial known-key comparison.

Never label a partial persistent-key scan "no drift" for the whole contract.

### 9.3 Freshness

A "clean" observation can become stale.

Promotion policy can define:
- max verification age;
- max ledger lag if current network ledger is available.

Stale verification → recheck required.

## 10. External deployment reconciliation

Workflow:
1. user selects environment/network and enters contract id;
2. validate network/contract identity;
3. fetch authoritative instance/executable;
4. capture observed WASM hash/latest ledger;
5. search/validate a user-supplied transaction hash if available;
6. compare against known artifacts;
7. create or attach to a `ContractTrack`;
8. mark unknown historical fields as unknown.

Never fabricate:
- deployer;
- constructor args;
- creation transaction;
- source hash.

An external deployment can still become `external-observed` and participate in environment diffs. Promotion policy may require stronger provenance before it can be a source.

## 11. Audit design

Each mutating registry transaction appends an event:

```ts
interface RegistryAuditEvent {
  id: string;
  sequence: number;
  eventType: string;
  entityType: string;
  entityId: string;
  actorLabel?: string;
  timestamp: string;
  previousEventHash?: string;
  eventHash: string;
  summary: string;
  details: unknown; // already redacted
}
```

Hash chaining is useful for local tamper evidence but does not turn browser storage into an externally trusted ledger.

Mirror a short sanitized summary to existing `auditTrail`; the registry event store remains the durable feature history.

## 12. Error/state model

Service errors should be typed:
- storage-unavailable;
- blocked-upgrade;
- unsupported-schema;
- validation-failed;
- stale-revision;
- duplicate-identity;
- network-mismatch;
- timeout;
- aborted;
- rpc-unavailable;
- contract-not-found;
- verification-failed;
- redaction-rejected;
- import-too-large.

UI states required by issue:
- loading;
- empty;
- success;
- error;
- retry;
- degraded;
- offline.

Offline:
- registry browsing/export can work from persisted data;
- chain verification and promotion readiness become stale/unavailable;
- execution is disabled.

## 13. UI decomposition

Recommended lazy tab:
`deploymentRegistry`

Panels/views:

**Registry**
- paginated/filterable contract tracks;
- environment/evidence/drift badges;
- latest artifact/version;
- last verification;
- keyboard-action menu.

**Environment diff**
- source/target selector;
- missing/extra/different contracts;
- artifact/evidence/drift/provenance differences;
- filter by gate impact.

**Promotion plan**
- selected source version;
- target observation;
- deterministic gate list;
- blocking reasons;
- explicit confirmation state;
- override UI only where policy permits.

**Provenance**
- source/WASM hashes;
- network passphrase identity;
- constructor inputs with redaction;
- deployer/ledger/tx evidence;
- evidence class explanation.

**Timeline**
- deployment versions;
- verification observations;
- external reconciliation;
- promotion attempts/outcomes;
- audit events.

**Import/export**
- redacted export by default;
- digest;
- import preview;
- conflict list;
- atomic commit/result.

Accessibility:
- semantic table headers;
- focus return after dialogs;
- live announcement of verification/promotion state changes without noisy polling;
- no color-only gate status;
- reduced-motion friendly timelines;
- responsive detail panels.

## 14. Existing-app integrations

### Compatibility

Before production promotion:
- consume a compatibility assessment/audit as one optional/required gate;
- store only stable/redacted evidence references in the plan;
- do not copy custom auth headers.

### Contract Storage

Link verification detail to current storage inspector, but keep drift code in a service returning typed evidence rather than scraping component state.

### Contracts

Existing deployment UI can call:
`registry.captureClaim(receipt)`
then:
`registry.verifyDeployment(id)`

Current synthetic receipt should land as `legacy-synthetic` until verified. If its generated contract id cannot be found on chain, it stays non-promotable.

### Audit

Mirror events as described above.

## 15. Test matrix

### Persistence / migration
- fresh DB creates all stores/indexes;
- v1→v2 migration preserves records;
- failed migration does not expose partially migrated DB;
- future logical export version rejected;
- interrupted multi-store mutation aborts all rows;
- stale entity revision rejects write;
- BroadcastChannel notification does not bypass persisted revision;
- localStorage fallback is never used for registry mutations.

### Identity / validation
- same contract id on different passphrases remains distinct;
- same network+contract identity duplicate is detected;
- malformed contract id rejected;
- missing/invalid SHA-256 rejected for verified artifacts;
- weak fallback digest cannot become verified artifact id;
- custom endpoint credentials are stripped/rejected;
- secret-key-shaped constructor value is redacted/rejected.

### Evidence
- simulation never satisfies confirmed-chain gate;
- legacy synthetic receipt never auto-upgrades;
- verified executable hash match → confirmed-chain;
- hash mismatch → drifted/block;
- RPC unavailable → unknown/unavailable/block;
- wrong network passphrase → mismatch/block;
- external observed contract preserves unknown constructor/deployer rather than inventing them.

### Drift
- executable hash matches;
- executable hash differs;
- Stellar asset/native executable handled explicitly;
- instance canonical hash matches/differs;
- persistent-key partial observation labelled partial;
- stale observation forces refresh;
- abort cancels pending RPC without committing stale result.

### Promotion
- clean source + clean target context can reach ready;
- source drift blocks;
- unavailable source blocks;
- stale source blocks;
- target changed after plan generation invalidates plan;
- production requires explicit confirmation;
- non-waivable gate cannot be overridden;
- allowed override expires and plan becomes blocked again;
- failed execution retains evidence/audit and does not mark target promoted;
- confirmed execution is independently read back.

### Import/export
- deterministic export digest;
- edit causes digest mismatch;
- oversized import rejected;
- duplicate identities previewed;
- mixed-invalid import makes zero writes;
- redacted=true export contains no secret/auth header;
- unknown future version rejected;
- old supported version migrated and revalidated.

### Cross-tab
- tab A edits entity, tab B stale save rejected;
- tab B receives revision invalidation and refetches;
- blocked/versionchange event surfaces recoverable UI;
- simultaneous unique identity creation yields one success/one conflict.

### Scale
- thousands of deployment versions remain paginated;
- environment diff does not load unbounded audit history;
- index-based filtering remains deterministic.

### UI / accessibility
- keyboard through tables/actions/dialogs;
- focus returns after reconcile/import/promotion dialogs;
- screen reader receives concise gate/result announcements;
- axe passes core views;
- reduced motion respected;
- offline/degraded states visible and actionable.

## 16. Required hostile proof

Do not only run green tests. Deliberately demonstrate at least:
1. synthetic receipt presented as confirmed → rejected;
2. weak 8-hex fallback artifact hash → rejected;
3. stale tab overwrite → conflict;
4. mid-transaction failure → no partial rows;
5. target executable changes between plan and confirmation → plan invalidated;
6. secret-like value in import/export → redacted/rejected;
7. RPC timeout → promotion blocked, not allowed;
8. external deployment missing historical metadata → unknown fields preserved.

Temporary hostile mutations/fixtures must not leak into production data.

## 17. 1,000+ production-line implementation decomposition

The issue's production-code minimum should be met naturally, not through padding.

A realistic cohesive decomposition:

- `src/types/deploymentRegistry.ts`: ~180–260 meaningful LOC
- `src/lib/deploymentRegistry/schema.ts`: ~180–240
- `repository.ts`: ~260–380
- `migration.ts`: ~100–180
- `redaction.ts` + `export.ts`: ~180–260
- `chainReader.ts`: ~180–280
- `drift.ts`: ~140–220
- `promotion.ts`: ~220–320
- `reconciliation.ts`: ~140–220
- `useDeploymentRegistry.ts`: ~180–260
- dashboard/components: several hundred additional meaningful LOC

This clears the minimum through necessary production behavior before tests/docs are counted.

## 18. Suggested implementation commits

1. `feat(registry): define versioned deployment evidence model`
2. `feat(registry): add atomic IndexedDB repository and migrations`
3. `feat(registry): verify Soroban executable and instance drift`
4. `feat(registry): reconcile external deployments and redacted import/export`
5. `feat(registry): add deterministic promotion planning and gates`
6. `feat(registry): add registry, diff, promotion and provenance dashboard`
7. `test(registry): cover migrations, stale tabs, drift, redaction and recovery`
8. `test(registry): add Playwright accessibility/recovery workflows`
9. `docs(registry): architecture, privacy, recovery and compatibility`

If an authoritative deployment executor is included:
- put it in a distinct commit with chain-confirmation tests;
- do not mix synthetic receipt cleanup invisibly into UI work.

## 19. Validation sequence after assignment

From a clean, current branch:

```bash
git status --short
npm ci
npm run lint
npm run format:check
npm run type-check
npm run test:coverage
npm run build
npm run test:e2e
npm run test:visual
npm audit --audit-level=high
git status --short
```

Also run targeted registry suites individually to preserve readable failure evidence.

If final rebase/update changes the head:
- rerun all required gates on the exact final head;
- record SHA + commands + exit codes.

## 20. Non-goals / guardrails

- Do not call a locally generated contract id proof of deployment.
- Do not call a local tx-like string an on-chain transaction hash.
- Do not use the weak hash fallback as artifact integrity.
- Do not silently fall back to localStorage for atomic registry writes.
- Do not copy API headers/tokens into registry/network exports.
- Do not fabricate unknown external-deployment provenance.
- Do not equate Kubernetes/canary risk scoring with Soroban promotion proof.
- Do not treat browser hash chaining as externally immutable audit.
- Do not auto-promote on missing/timeout/unknown verification.
- Do not bypass final CI because the implementation exceeds 1,000 production lines.

The strongest implementation is one where every “Verified”, “Clean”, and “Ready to promote” badge can be traced back to exact cryptographic/network evidence and a deterministic gate result.
