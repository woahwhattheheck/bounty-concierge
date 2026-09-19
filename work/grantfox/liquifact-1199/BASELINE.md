# GrantFox baseline — Liquifact/Liquifact-contracts #1199

Operation: `GFOX2-20260919-096`
Worker: ZZ-Moraine-29 / GPT-5.6 Sol
Observed: 2026-09-19
Upstream default branch: `main`
Pinned upstream head: `d5a08576e02bf9626e4f82b3b4efd01ec2af27d2`

## Canonical issue state

- GitHub issue: Liquifact/Liquifact-contracts #1199 — namespaced storage keys with TTL/bump management for escrow.
- State at census: OPEN, GitHub-unassigned.
- GrantFox page: Unassigned; one application per user; three pre-existing applicant comments at census.
- Matching open PR census at TAKE: no open PR referencing #1199.
- Labels include GrantFox OSS / Maybe Rewarded / FWC26. Those labels are eligibility signals only; no fixed award or payment is asserted here.
- GrantFox's contributor flow says official assignment is the signal to start implementation. This packet therefore stops at source census + assignment-ready plan.

## Current-main finding: issue is partially implemented

The issue description is stale if read as "there is no centralized key layer and no TTL handling."

Pinned source evidence:

- `escrow/src/keys.rs` blob `bf15d509483d331524d68b5b030f05212160bc50`
- `docs/escrow-gas-storage-notes.md` blob `72fae5081e36a1e47ccc432fb852dd8edbe0a982`
- `escrow/src/lib.rs` blob `b1532440af33744b6d3c55189407b1e65d62119d`

`keys.rs` already defines typed constructor helpers over the existing `DataKey` enum and documents the additive-key / no-discriminant-drift policy. The file also states the remaining gap explicitly: real call sites currently still use `DataKey::Variant` literals inline, while the helpers remain available for migration.

TTL support also already exists:

- per-investor persistent keys are extended at **write time** with `PERSISTENT_TTL_MIN_EXTENSION_LEDGERS`;
- the contract exposes permissionless `bump_ttl`;
- storage documentation distinguishes instance and persistent lifetimes.

Historical PR #426 already added write-time TTL extension. Historical key-centralization work also exists. A new contribution must therefore finish the remaining acceptance gap rather than duplicate those predecessors.

## Assignment-safe implementation contract

### 1. Finish call-site centralization without changing storage identity

Route remaining escrow storage construction sites through the existing `keys` module.

Compatibility constraint:

- preserve the exact existing `DataKey` variants and discriminants;
- preserve stored value types;
- preserve XDR/storage-key identity;
- do **not** add a migration for this refactor.

Required regression: for every migrated key family, helper-built and legacy raw `DataKey::Variant` values must address the same stored entry.

### 2. Centralize TTL policy by storage class

Do not conflate existing write-time extension with the issue's requested access-time policy.

Define one explicit policy seam that states which long-lived entries are:
- instance storage,
- persistent storage,
- extended on write,
- extended on access,
- eligible for permissionless maintenance via `bump_ttl`.

Access-time extension must never shorten an entry's lifetime and must not mutate business balances, status, authorization, or accounting state.

### 3. Retained tests

Minimum matrix:

1. same logical input -> same storage key;
2. helper-built key == legacy raw-key identity for every migrated family;
3. cross-feature key matrix has no accidental collisions;
4. access to a long-lived eligible entry extends the intended TTL horizon;
5. ineligible/read-only access does not invent unrelated storage writes;
6. legacy state created under raw variants remains readable through helpers;
7. instance and persistent TTL behavior are tested separately;
8. no migration path is invoked or required.

Use Soroban ledger/testutils where needed to advance the ledger and inspect TTL behavior mechanically.

### 4. Documentation and gates

Update the storage-layout/lifetime documentation with one canonical table for key family, storage class, lifetime policy, and access/write bump semantics.

Assignment implementation should finish with:
- `cargo fmt --check`
- `cargo clippy --all-targets -- -D warnings`
- `cargo test`

Report exact commands and any unrelated upstream baseline failures separately.

## Source-specific GrantFox application text

> I reviewed current `main` before applying. #1199 is partially implemented already, so I would not duplicate existing work: `escrow/src/keys.rs` provides typed constructors, write-time TTL extension already exists for persistent investor state, and the contract already exposes `bump_ttl`. The remaining gap is explicit in current `keys.rs`: real call sites still construct `DataKey::Variant` inline, while the issue also asks for access-time lifetime management without changing key layout.
>
> My plan is to migrate the remaining call sites through the existing key module while preserving every current variant/discriminant/XDR identity; centralize instance-vs-persistent TTL policy and add access-time extension only where appropriate; add compatibility tests proving helper-built and legacy raw keys address the same state plus a cross-feature collision matrix; and add Soroban ledger/testutils coverage proving eligible reads extend TTL without changing balances or other business state. I will update the storage-lifetime docs and run fmt/clippy/tests. No archival tiering or storage migration is in scope.
>
> I am ready to implement this focused compatibility refactor if assigned.

## Publication / authority notes

The linked GitHub integration can read the upstream repository but its collaborator-permission probe returned 403 `Resource not accessible by integration`. That is a connector-access fact, not a claim about the human account's broader GitHub permissions.

A GrantFox browser application run was started from the live issue page and remains pending at the time this packet was written. No successful application, assignment, reward, or payment is claimed until a concrete provider receipt exists.

No upstream source, issue assignment, escrow state, wallet, payment, or reward state is mutated by this baseline.
