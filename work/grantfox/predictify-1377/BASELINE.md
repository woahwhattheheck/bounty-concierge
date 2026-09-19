# Predictify #1377 source baseline

Source-readiness packet for `Predictify-org/predictify-contracts#1377`, pinned to
`master@b0d4a16d15ae2e71c43a4200ed5fd3a62bbb4163`.

GitHub currently shows the issue open and unassigned, with two prior application
comments and no targeted open PR carrier. The issue requires maintainer assignment
before implementation; this packet does not change upstream source.

## Residual source gap

- `storage.rs` blob `fb77bce630a35f0b1aa3b45449856d16a918554f` claims
  `tests/datakey_collision.rs` proves unique XDR encodings for `DataKey`.
- `Cargo.toml` blob `f7b29a731621edd859f0f5c74f8f3865fe81c784` actually
  disables that test because of SDK incompatibilities. The live `.rs` file is absent;
  only `.rs.disabled` remains (blob `204cc60e74c31d3c6db5fd81479c546cb337b0a6`).
- The disabled test constructs only three variants, while current `DataKey` has many.
- Storage identity also spans oracle `DataKey` (`b85cfc6b...`), governance
  `StorageKey` (`b68d1aa3...`), and raw/composite keys such as event tuples,
  balance `Vec<Val>` keys, archive-derived keys, and migration symbols.
- `docs/STORAGE_TIER.md` blob `ec3ee04c...` itself records decentralized tier
  assignment and stale audit/doc drift; it is not an authoritative byte manifest.
- Both `migrate_v1_to_v2` and `migrate_v2_to_v3` are explicit placeholders:
  each says real data migration is not implemented, then sets `markets_migrated = 1`.
- Repository search found no complete golden key-byte manifest and no
  `test_storage_migration` suite.

## Assignment-time plan

1. Generate one executable manifest for every live key family/raw key shape, including
   representative canonical Soroban/XDR bytes and storage class.
2. Replace the disabled three-variant check with CI that covers all families and
   cross-family collisions; new keys require intentional manifest updates.
3. Replace placeholder migrations with empty/populated/malformed legacy fixtures and
   fail before partial mutation on unsupported generations.
4. Bind storage-schema compatibility into the existing authenticated upgrade path
   without replacing its admin/WASM predecessor checks (`upgrade_manager.rs` blob
   `95f4b821...`).
5. Validate human storage documentation against the executable manifest.

Required regressions: golden bytes for every family, deliberate collision/reorder
failure, raw-key omission failure, populated logical-value preservation, malformed
migration rejection before mutation, empty migration without invented records, and
unchanged upgrade authentication/predecessor-chain behavior.

Implementation remains on hold until maintainer assignment.
