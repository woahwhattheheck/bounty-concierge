# soroban-ajo #892 upgrade rollback source baseline

Source-readiness packet for `Ajo-contrib/soroban-ajo#892`, pinned to
`master@1b87ea7344ae3d871e54abff05eabe5113bd2956`.

Live GrantFox readback on 2026-09-19 showed **Unassigned**, **Apply to this
issue**, zero comments, and one application per user. GitHub shows the issue
open, unassigned, with zero comments. The issue is labeled Maybe Rewarded; this
packet does not assume an award, amount, assignment, or payout.

## Current source reality

The repository now has more schema-upgrade machinery than the August issue
description suggests, but it still does **not** have a coherent rollback
contract.

`contracts/ajo/src/contract.rs` exposes:

```text
upgrade(env, caller, new_wasm_hash, schema_version)
```

The current implementation authenticates the admin, calls
`ensure_supported_schema`, requires the **caller-supplied**
`schema_version == CURRENT_SCHEMA_VERSION`, and then directly calls
`update_current_contract_wasm(new_wasm_hash)`.

That has four important consequences:

1. The gate proves that the **current** Wasm understands the currently stored
   schema. It does not prove that `new_wasm_hash` understands that schema.
   There is no hash -> supported-schema binding in contract state.
2. A previous same-schema Wasm may be mechanically reinstallable if the operator
   still knows its hash and that binary is genuinely compatible, but the
   contract does not prove that compatibility.
3. After a non-additive N -> N+1 migration, old N code cannot be assumed to read
   N+1 state. A safe downgrade therefore requires either forward compatibility
   that was deliberately built into N **before** N+1 existed, or a reversible
   N+1 -> N data migration executed by the N+1 code before switching Wasm.
4. Merely replaying an old Wasm hash is not a rollback model.

## Documentation contradiction

The current repository simultaneously says two incompatible things:

- `contracts/ajo/docs/DEPENDENCY_POLICY.md` correctly warns that an on-chain
  contract Wasm is not something operators can "quietly revert".
- `docs/DEPLOYMENT.md` has a **Contract rollback** section telling operators
  to reinstall the previous Wasm and call `upgrade` with
  `--new_wasm_hash $PREV_WASM_HASH`.

The deployment command is also stale against the live ABI: it omits the required
`schema_version` argument.

`contracts/ajo/docs/storage_migrations.md` documents a fail-closed additive-only
upgrade gate and a future vN -> vN+1 migration recipe, but it never defines the
reverse direction or says when rollback is forbidden.

## Missing evidence / audit surface

Repository search found no contract-level:

- stored previous/current Wasm hash history;
- Wasm-hash -> supported-schema manifest;
- dedicated rollback entrypoint;
- upgrade/rollback audit event;
- N -> N-1 reverse migration;
- rollback regression that populates state, upgrades, mutates state, reverses,
  and proves old code can read it.

The existing `upgrade_migration_tests.rs` is valuable but narrower: it proves a
populated v1 contract rejects a request declaring schema v2 and that state stays
readable. It does not execute a successful upgrade or downgrade.

## Recommended issue resolution

For this issue's stated 3-6 hour scope, the safest bounded resolution is to make
the operational contract explicit rather than pretend generic rollback exists.

### Policy A — forward-only across schema generations

Document and enforce these rules:

1. **Same-schema code revert:** allowed only when the target Wasm is an exact
   previously reviewed artifact whose storage/API compatibility with the current
   schema is independently established.
2. **Schema-changing upgrade:** forward-only by default. Once state is migrated
   to N+1, operators must not reinstall N code unless a reviewed reverse
   migration contract exists.
3. **Pause before upgrade:** keep mutation paused until post-upgrade reads and
   state invariants are verified.
4. **Deployment receipt:** retain exact old/new Wasm hashes, schema version,
   source commit, build/toolchain identity, and operator transaction receipt.
5. **Fix the runbook:** every `upgrade` CLI example must include the current
   `schema_version` argument and must not call a cross-schema hash replay a
   rollback.

This directly answers #892 without creating a false safety net.

### Policy B — true N-1 rollback (larger follow-up)

If maintainers require real cross-schema rollback, the implementation needs more
than a previous hash:

1. define an auditable compatibility manifest for an approved target Wasm hash;
2. retain the prior approved Wasm hash + schema identity before each upgrade;
3. implement an explicit reverse migration N -> N-1 under the **current N**
   code, including legacy types and failure-atomic writes;
4. change the stored schema only after the reverse migration succeeds;
5. switch to the approved N-1 Wasm only after state is N-1-readable;
6. emit an upgrade/rollback event containing from/to hash and from/to schema;
7. reject rollback when no reviewed reverse migration exists.

A simple `previous_wasm_hash` slot without reverse-state semantics would be
dangerous because it could make an unsafe downgrade easier to invoke.

## Regression matrix after assignment

Minimum hostile coverage:

- non-admin upgrade remains rejected;
- declared schema != current schema remains rejected before Wasm mutation;
- same-schema upgrade path uses the exact approved artifact identity;
- deployment example contains the required schema argument;
- rollback runbook refuses cross-schema hash replay without reverse migration;
- populated state survives a failed incompatible upgrade unchanged;
- if same-schema revert is supported, populated state remains readable before
  and after forward + revert;
- if N -> N-1 is implemented, mixed/partial reverse migration fails atomically,
  schema stamp does not move early, and old Wasm reads representative populated
  state after downgrade;
- every successful code transition has an auditable from/to receipt/event;
- pause/unpause ordering cannot expose partially validated code to mutations.

Current package: `soroban-ajo`. Use `cargo test --locked` in
`contracts/ajo`; keep the existing populated migration regression in scope.

## Application-ready note

> Hi maintainers, I audited #892 against
> `master@1b87ea7344ae3d871e54abff05eabe5113bd2956`. The repo now has a
> v1 schema gate, but rollback is still not safely defined: `upgrade` only
> compares a caller-supplied schema number with the current Wasm's constant, so
> it does not prove the target Wasm can read current state. The deployment
> runbook currently tells operators to reinstall a previous Wasm and also omits
> the now-required schema argument, while the dependency policy correctly warns
> contract Wasm cannot be quietly reverted. I can take this after assignment.
> My bounded recommendation is explicit forward-only behavior across schema
> generations plus a correct same-schema revert/runbook contract; true N-1
> rollback should require a reviewed reverse migration and auditable target
> artifact identity, not just replaying an old hash. Please assign me and confirm
> whether you want the bounded policy/runbook fix or a larger reversible-migration
> follow-up.

Disposition: **WAIT_FOR_PROVIDER_ASSIGNMENT; SOURCE_CONTRADICTION_CONFIRMED**.
