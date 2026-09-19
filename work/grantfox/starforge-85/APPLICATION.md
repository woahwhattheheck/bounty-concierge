# GrantFox application text — StarForge #85

**Status:** prepared only; not submitted from this seat.

I traced StarForge #85 against `master@c2311c6cfbe25ab841ad6a18be60df0fe4f2fe61` and would like to take the ledger-snapshot/offline-replay implementation.

I also checked the current overlapping work rather than duplicating it. Open PR #114 (for #87) adds privacy-safe JSON-RPC traffic recording and deterministic **response replay**. I would reuse/alignment-fit its redaction/private-persistence/digest primitives if it lands, but #85 still needs a separate ledger-state snapshot and local Soroban host path: replaying a recorded `simulateTransaction` response is not offline execution against immutable ledger entries.

My implementation plan is:
- add a versioned deterministic snapshot schema carrying network identity, ledger metadata, canonical ledger-key/entry XDR, contract code/data, TTL evidence, capture provenance, per-entry hashes and an aggregate archive digest;
- capture from the simulation footprint through an injectable bounded RPC transport, with strict key/byte/batch/deadline limits and explicit missing-entry handling;
- write owner-only deterministic archives with secret/endpoint redaction, size/decompression/zip-slip protections, offline verification, semantic diff, forward migrations, and safe pruning;
- add a snapshot-backed Soroban host adapter that resolves ledger reads only from immutable captured entries, never silently falls back to network, rejects network/protocol mismatches, and clearly states replay is not validator/consensus equivalence;
- expose `starforge snapshot capture|inspect|verify|diff|replay|migrate` (plus prune if kept as a distinct command) with stable versioned JSON and predictable non-zero failure behavior;
- cover deterministic bytes, corruption, duplicate/unsafe archive entries, bounds, TTLs, missing entries, migration/version drift, network mismatch, unsupported host functions, zero-network replay, redaction, permissions, and CLI/e2e behavior.

One source detail I would avoid building on blindly: current `src/utils/soroban.rs` still contains explicit mock/placeholder XDR paths (including mock transaction XDR and debug-string ledger-key encoding), while `src/commands/query/executor.rs` already demonstrates proper `stellar-xdr` encoding and a testable transport trait. I would use canonical XDR and a shared/injectable transport boundary rather than promote the mock helpers into snapshot truth.

If assigned, I can carry this through the repository's full final-SHA gates and keep the PR conflict-free with current `master`.
