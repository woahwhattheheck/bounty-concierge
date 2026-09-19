# StarForge #85 — portable ledger snapshot / offline replay source baseline

**Classification:** source-readiness / application packet only. This is **not** an upstream bounty delivery and must not be counted as one.

- Issue: `StarsForges/StarForge#85`
- Upstream default branch: `master`
- Source pin: `c2311c6cfbe25ab841ad6a18be60df0fe4f2fe61`
- Live issue/provider census on 2026-09-19: GitHub OPEN, no assignee, no Development carrier; GrantFox **Unassigned**, Apply enabled, 2 prior applicant comments.
- Current connector permission on upstream: pull/read only; no push.
- Issue minimum: at least 1,000 lines of meaningful production Rust, excluding tests/docs/generated/vendor/format churn.

## The key architectural distinction

There is a major adjacent upstream carrier that must be reused rather than duplicated:

- **PR #114** `feat(rpc): implement privacy-safe rpc traffic recording, redaction, and deterministic replay`
- closes upstream issue #87
- base: `master@c2311c6cfbe25ab841ad6a18be60df0fe4f2fe61`
- head: `7f86285384b5161a5fd6051e44ac6f4f1ed5929a`
- open / mergeable at census time
- 19 changed files, ~3,939 additions
- adds `src/utils/rpc_recording/`, RPC record/replay/sanitize/verify CLI, redaction, 0600 files, digests, causal replay, fault injection.

That PR replays **recorded JSON-RPC request/response traffic**. It does **not** implement #85's core requirement: capture immutable ledger state and execute supported Soroban transaction simulations locally against that state. Returning a previously recorded `simulateTransaction` response is response replay, not offline host execution.

If #114 lands before #85 implementation, reuse its privacy/redaction, private persistence, digests, and possibly generic replay fixtures. Do not duplicate its `starforge rpc` command family. If it remains open, keep #85 independently mergeable while aligning shared primitives instead of making #85 depend on an unmerged fork commit.

## Current-source seams worth reusing

### 1. Real transport seam exists in the query subsystem

`src/commands/query/executor.rs` already defines:

- a `RpcTransport` trait with `endpoint()` and `call(method, params)`;
- `HttpRpcTransport` with endpoint validation;
- a read-only RPC allowlist;
- deterministic fixture-friendly execution;
- proper `stellar-xdr` encoding for a contract-instance `LedgerKey`.

For #85, promote/generalize the transport boundary into shared code (or introduce a dedicated `SnapshotRpc` trait) so capture tests can use an in-memory transport and can prove that replay performs **zero network calls**.

Do not make snapshot domain logic call `ureq` directly.

### 2. Existing general Soroban helper is not production-ready enough for snapshot truth

`src/utils/soroban.rs` has useful public result shapes such as `StorageFootprintSummary`, but several critical internals are explicitly simplified/mock:

- `ledger_key_to_xdr_base64()` base64-encodes a debug string instead of canonical XDR;
- `build_transaction_xdr()` returns `mock_transaction_xdr_...`;
- address argument encoding contains a zeroed placeholder account;
- `parse_contract_inspect_result()` returns a placeholder WASM hash and empty instance storage.

Therefore #85 must not treat `utils::soroban` as an authoritative ledger-capture codec. Reuse shapes only where they preserve the raw canonical bytes; use `stellar-xdr` for real `LedgerKey` / ledger-entry XDR and retain the original base64 bytes needed for replay.

### 3. Deterministic archive / migration patterns already exist

`src/utils/release/` provides strong patterns:

- deterministic zip entry order/timestamps;
- SHA-256 checksums;
- versioned manifests;
- schema-first migration with hard rejection of newer unsupported versions;
- offline verification that aggregates all checks rather than failing at the first one;
- rollback-safe staging.

Reuse the pattern, not the release format verbatim. The release archive helper stamps archive entries with mode `0755`; snapshot material should be written through an owner-only atomic/private path and archive entry permissions should not advertise executable/world-readable semantics.

### 4. Private atomic persistence already exists

`src/signer_rotation/store.rs` provides `write_private_text_atomic()` / private-directory helpers with:

- temporary-file + sync + atomic rename;
- Unix `0600` file permissions;
- `0700` directories;
- cleanup on failure.

`src/commands/query/output.rs` also has owner-only output and no-overwrite-by-default behavior. Snapshot archive writes should follow this security posture.

### 5. Stable machine output conventions exist

The query/compatibility command families:

- separate serializable domain models from terminal rendering;
- expose explicit schema-version strings;
- support human vs stable JSON formats;
- mark machine-readable invocations in `main.rs` to suppress decorative output;
- keep network transport injectable/testable.

`starforge snapshot ... --format json` should follow the same pattern and be added to the global machine-readable gate.

## Recommended module boundary

A coherent implementation naturally clears the 1,000-production-LOC minimum without padding:

```
src/
  snapshot/
    mod.rs          # shared public API
    model.rs        # versioned archive/domain types + invariants
    transport.rs    # bounded RPC capture trait + HTTP implementation
    capture.rs      # footprint expansion / ledger entry acquisition
    archive.rs      # deterministic private zip read/write + limits
    verify.rs       # structural/integrity/network/schema/security checks
    diff.rs         # deterministic semantic diff
    migrations.rs   # explicit forward migrations
    replay.rs       # immutable snapshot-backed Soroban host adapter
    prune.rs        # safe bounded reduction / reachability policy
  commands/
    snapshot.rs     # clap args + rendering only
```

If maintainers prefer `src/utils/snapshot/` rather than a top-level shared-domain module, preserve the same separation: command parsing/rendering must not own capture, archive, diff, migration, or host semantics.

## Snapshot v1 contract

Use a stable top-level schema ID such as `starforge.ledger-snapshot/v1` and keep canonical content independent of terminal presentation.

Minimum manifest fields:

- schema/version;
- snapshot ID derived from canonical content, not random generation;
- capture tool version + source commit when available;
- network name plus **network passphrase/network-id digest**;
- protocol/RPC capability metadata used during capture;
- captured ledger sequence and close time where available;
- sanitized RPC origin/source metadata (never credentials/query secrets);
- capture bounds actually enforced;
- root transaction/simulation identifier or canonical envelope hash;
- complete normalized footprint roots;
- entry table sorted by canonical ledger-key XDR;
- per-entry:
  - canonical ledger-key XDR;
  - canonical ledger-entry XDR;
  - access mode (read-only/read-write where known);
  - last-modified ledger sequence;
  - live-until ledger sequence / TTL evidence when applicable;
  - entry byte length;
  - SHA-256 of exact canonical bytes;
  - source/proof metadata needed to explain how it was captured;
- aggregate entry-set digest;
- archive member digest table;
- explicit redaction report;
- warnings/capability limitations.

Do not serialize secret signing material, local wallet seeds, bearer/API tokens, cookies, or full credential-bearing RPC URLs.

### Determinism rule

Given the same normalized manifest, entry bytes, schema version, compression settings, and source-date value, export must be byte-identical.

Sort ledger records by canonical key bytes (or their stable encoded form), not hash-map iteration order. Normalize archive timestamps. Reject duplicate canonical keys.

## Capture algorithm

A production capture path should be bounded and explicit:

1. Obtain or accept the transaction/envelope to simulate.
2. Call live RPC with a bounded timeout and obtain the simulation response/footprint.
3. Preserve canonical footprint ledger-key XDR; do not round-trip through current mock debug encoders.
4. Deduplicate and sort keys.
5. Enforce limits **before** expansion:
   - max roots;
   - max ledger entries;
   - max per-entry bytes;
   - max aggregate uncompressed bytes;
   - max archive bytes;
   - max RPC batch size / number of batches;
   - total capture deadline.
6. Fetch exact ledger entries in bounded batches with `getLedgerEntries`.
7. Record missing keys explicitly; never silently omit them.
8. Capture TTL/live-until evidence and ledger metadata tied to the same network identity.
9. If a contract instance references a WASM hash required by local execution and that code is not already in the footprint result, resolve the required contract-code entry through a bounded, declared expansion rule. Every derived key must be recorded with provenance/rationale.
10. Redact metadata before persistence.
11. Verify the in-memory snapshot, then atomically write a deterministic private archive.
12. Re-open and verify the emitted archive before reporting success.

"Footprint-driven" must not become unbounded graph crawling.

## Offline replay is local execution, not RPC-response replay

Current `Cargo.toml` includes `stellar-xdr` and `wasmi` but no Soroban host implementation crate. Raw `wasmi` is not a substitute for Soroban host semantics.

Before coding, the assigned implementer should pin a compatible Soroban host/runtime dependency (for the repository's Stellar/XDR generation) and implement an adapter whose ledger lookup resolves only from immutable snapshot entries.

The adapter must:

- refuse network access;
- validate snapshot network identity before execution;
- resolve ledger reads from captured canonical key/value bytes;
- expose recorded TTL/ledger metadata consistently;
- fail deterministically on missing entries;
- reject unsupported host/protocol capabilities with an explicit machine-readable error;
- never silently fall back to live RPC;
- treat snapshot data as untrusted input and validate it before host construction;
- report that replay is a deterministic debugging/simulation aid, **not consensus-level equivalence with a validator**.

A test transport that panics on any call should wrap replay tests; a passing replay must prove that no network function was invoked.

## Archive security / hostile-input rules

Snapshot archives are untrusted files. Verification/load must reject:

- absolute paths and `..` zip-slip members;
- duplicate archive member names;
- duplicate canonical ledger keys;
- undeclared members;
- decompression bombs / declared-size mismatches;
- files or aggregate uncompressed content over configured limits;
- malformed base64/XDR;
- hash mismatches;
- network/passphrase mismatch;
- unsupported newer schema versions;
- impossible ledger/TTL metadata;
- secret-shaped material in fields that should have been redacted.

Write archives through owner-only atomic storage. Do not directly reuse the release archive helper's fixed `0755` entry permissions.

## Diff / prune / migrate semantics

### Diff
Compare normalized semantic records, not pretty JSON:

- network identity/protocol;
- ledger sequence metadata;
- added/removed/changed canonical keys;
- TTL changes;
- code hash changes;
- entry-byte hash changes;
- manifest/capture-bound changes.

Output ordering must be stable.

### Prune
Pruning must never mutate an input archive in place by default. Emit a new snapshot with new digest/provenance. A pruned snapshot must either remain replay-complete for the declared root workload or be explicitly marked incomplete/non-replayable.

### Migrate
Follow the existing release migration engine style:

- parse schema-agnostic JSON first;
- apply registered pure `vN -> vN+1` transformations;
- preserve unknown-safe metadata where specified;
- hard-reject newer-than-supported versions;
- verify again after migration;
- never silently down-convert.

## CLI contract

Required family:

- `starforge snapshot capture`
- `starforge snapshot inspect`
- `starforge snapshot verify`
- `starforge snapshot diff`
- `starforge snapshot replay`
- `starforge snapshot migrate`

Add `prune` if that is the chosen surface for the issue's pruning requirement.

Every command should support stable JSON for automation where applicable; JSON mode must suppress banners. Keep exit behavior predictable: success 0, validation/operational failure non-zero, with a stable JSON error/status code rather than parsing human prose.

## Test matrix

### Format / archive
- same logical input -> byte-identical archive;
- different entry bytes -> digest changes;
- reordered input keys -> identical archive;
- corrupt member / manifest / digest rejected;
- zip-slip / absolute member rejected;
- duplicate member/key rejected;
- oversize member and decompression bomb rejected;
- private output permissions verified on Unix;
- newer schema rejected;
- v1 migration no-op and future registered migration fixture.

### Capture
- bounded RPC batch behavior;
- timeout contextual error;
- missing ledger entry is explicit;
- contract-code expansion stays within declared bounds;
- TTL evidence preserved;
- network identity recorded;
- secret-bearing endpoint metadata redacted;
- mock transport gives deterministic archive bytes.

### Replay
- deterministic identical result on repeated run;
- replay succeeds with a transport that panics if contacted;
- missing entry fails deterministically;
- modified entry/digest fails before host execution;
- network mismatch rejected;
- unsupported host function/protocol rejected explicitly;
- read-write footprint behavior is isolated to the replay host and never mutates the archive;
- archive remains byte-identical before/after replay.

### Diff / prune
- stable ordering;
- code/data/TTL/network changes classified correctly;
- prune emits a new digest and cannot claim replay-complete if required roots were removed.

### CLI
- help exposes every required command;
- human output contains no secrets;
- JSON output parses and keeps stable schema IDs;
- no banner contaminates JSON;
- overwrite requires explicit intent;
- failure returns documented non-zero status;
- no live-network dependency in CI.

## Integration / sequencing recommendation

1. Coordinate with PR #114 instead of duplicating RPC recording/replay/redaction.
2. Land the snapshot **model + archive + verify + migration + deterministic diff** foundation.
3. Land bounded live capture over injectable transport.
4. Add the real snapshot-backed Soroban host adapter and replay.
5. Add prune + CLI/e2e/docs.
6. Rebase final branch onto current `master`, run every issue-mandated gate on the final SHA:
   - `cargo fmt --all --check`
   - `cargo deny --all-features check`
   - `cargo build --locked`
   - `cargo test --locked`
   - `cargo clippy --all-targets --all-features --locked -- -D warnings`
   - `./scripts/e2e-smoke.sh`

No CI relaxation, ignored failures, or placeholder host behavior qualifies.

## Pinned evidence

- `Cargo.toml` blob `e2bd210dd1e5e4f4ff8170d3e9dd25cd73944d53`
- `src/main.rs` blob `349e092c89432709304100a98448dfd862e01480`
- `src/lib.rs` blob `d05a42aa43f70b7e13af155752f937ae02f1f754`
- `src/commands/mod.rs` blob `e0a1165d3307473c96d1a4870894a151bdd4fe67`
- `src/utils/soroban.rs` blob `ebc77f8b572288c03db17c6ec222f58389267029`
- `src/commands/query/executor.rs` blob `0443548f912e08d9c3121269acf87ead6353927e`
- `src/commands/query/output.rs` blob `8db4de95a4250cabcffdbcae19f6404c59f1fb00`
- `src/utils/release/archive.rs` blob `f7de862cc4c6c50f1c07da8f7028ec6979d5daf1`
- `src/utils/release/manifest.rs` blob `672fe8cf3ce6fdf3b4bf0bb8375d48c979dce7f0`
- `src/utils/release/migrations.rs` blob `47ace4e8a4a8b578a9df76c48d57cbf9e3c8055e`
- `src/utils/release/verify.rs` blob `dcfb8567daa1a66e9d3b46a23e4196ad734e0222`
- `src/signer_rotation/store.rs` blob `348d74a9e5c79c4e750519dbc617fd4f970d57d6`
- adjacent upstream PR #114 head `7f86285384b5161a5fd6051e44ac6f4f1ed5929a`.

## Current gate

Provider assignment remains **Unassigned**. Do not begin the 1,000+ LOC upstream implementation under the swarm's assignment rules until provider/maintainer authority changes. The application text in this directory is source-specific and ready for an authenticated GrantFox/GitHub user surface.
