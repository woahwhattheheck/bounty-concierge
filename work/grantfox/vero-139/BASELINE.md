# GrantFox baseline — Vero-protocol/vero-core-contracts #139

Operation: `GFOX3-20260919-vero-139/R-storage-compat-census`  
Worker: ZZ-Rook-73 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `bad5c54092afdf3285bd60998341a1c27ec0eab6`

## Canonical issue

- GitHub: https://github.com/Vero-protocol/vero-core-contracts/issues/139
- GrantFox: https://contribute.grantfox.xyz/org/Vero-protocol/repo/vero-core-contracts/issue/139
- State at observation: OPEN
- GitHub assignee: none
- Labels observed: `good first issue`, `Maybe Rewarded`, `GrantFox OSS`, `Third Campaign`
- Existing issue comments: 2 application comments
- Matching PR census: no #139 / AllVotes carrier surfaced in the fresh connector search
- Current swarm census before publication: zero Slack messages for `Vero-protocol`
- Reward interpretation: campaign / Maybe Rewarded labels are eligibility signals only; no fixed amount, award, or payment is asserted.

This is a pre-assignment research packet. The linked upstream repository is readable through the connector but not writable (`pull=true`, `push=false`). No upstream source mutation is performed here.

## Source-bound finding

The issue's narrow claim is supported by the pinned source.

`src/contracts/storage_layout.rs` blob `625e0003644f2057d0fb8b608c43df6a2aa4ea3a` defines `DataKey::AllVotes` as a unit variant described as a global vote-record sequence.

The actual voting path at the same head uses:

- `DataKey::Voted(task_id, guardian)` to enforce one vote per task/guardian.
- `DataKey::TaskVoters(task_id)` via `storage::append_task_voter` to retain per-task voter addresses.
- The task record itself to retain aggregate vote count / weight.

Pinned blobs:
- `src/contracts/voting.rs`: `9ac20f707ef387d4f1a4334a3dd1d43bb90c95dc`
- `src/storage.rs`: `a6d2685cbc4b83e2407bc68a48826a2d25383b92`
- `src/task.rs`: `74d72365c5655ac73fca7815a57a78de3af72891`
- `src/types.rs`: `fdeccc20980bd625b959aef7c21ed8f717afe9b5`
- `src/migrate.rs`: `2b6229548d5cbfc8ccab95915e2629fa85cb03aa`

In the inspected vote/storage/task/migration paths, `AllVotes` is never read, written, removed, migrated, or used for purge. Purge instead removes `Voted(...)` records and `TaskVoters(task_id)`.

## Compatibility correction: do not reason in Rust ordinal space

The issue text says the dead variant "occupies a discriminant." That wording is easy to over-read.

`DataKey` is a Soroban `#[contracttype]` enum, not a `#[repr(u32)]` integer enum. Soroban SDK documentation states that:
- a tuple contract enum is stored as a vector whose first element is the **variant name as a Symbol** and whose next element is the payload;
- a unit contract enum is stored as a one-element vector containing the **variant name as a Symbol**;
- integer-valued enums are a separate representation.

Reference: https://docs.rs/soroban-sdk/latest/soroban_sdk/attr.contracttype.html

Therefore removing a genuinely unused unit variant does **not** renumber the serialized keys for neighboring variants. Existing keys such as `Voted`, `TaskVoters`, `AllTasks`, `AllRewardStreams`, and `StorageVersion` remain keyed by their symbolic variant names.

This does not prove that deletion is automatically safe in every deployment. A maintainer should still confirm that no released/off-chain client or historical binary depends on the `AllVotes` type member and that no on-chain state was ever written under the `AllVotes` symbol. But the relevant compatibility question is symbolic-schema/API history, not enum ordinal drift.

## Narrow implementation recommendation after assignment

Prefer **removal**, not inventing a new global vote index, unless maintainers explicitly want an analytics index.

1. Reconfirm the latest default-branch head and search all source/tests/generated bindings for `AllVotes`.
2. Check release / deployed-contract history for any earlier implementation that wrote `DataKey::AllVotes`; if historical use exists, retain/reserve the variant rather than deleting it casually.
3. If still dead, remove only the `AllVotes` variant and its misleading comment.
4. Add a focused storage-encoding regression demonstrating representative existing `DataKey` encodings remain stable across the change (especially `Voted`, `TaskVoters`, and a later-declared key such as `StorageVersion` or `TaskIndexAt`).
5. Run the full Rust/Soroban suite and the repository's formatting/lint commands.
6. In the PR description, document why no global vote index is being introduced and why removal does not renumber `#[contracttype]` keys.

Do **not** add an `AllVotes` write path merely to make the variant "used": that would create a new unbounded global collection and a second vote index without a demonstrated consumer.

## Application-ready note

> I audited #139 against current `main@bad5c54092afdf3285bd60998341a1c27ec0eab6` before applying. The current vote path records `Voted(task_id, guardian)` plus `TaskVoters(task_id)`; purge removes those records, and the inspected vote/storage/task/migration paths never use `AllVotes`. I would prefer removing the dead variant rather than inventing an unbounded second vote index, after one final latest-head/history check.
>
> I also checked the storage-compatibility concern: `DataKey` is a Soroban `#[contracttype]` enum, whose unit/tuple variants are encoded by symbolic variant name rather than Rust ordinal. I would add a focused encoding regression for representative existing keys, run the full contract tests/lints, and document the compatibility reasoning in the PR. I will wait for maintainer/GrantFox assignment before changing upstream source.

## Authority / limits

- Upstream implementation is assignment-gated by this swarm lane.
- Upstream connector permission observed: `pull=true`, `push=false`.
- No issue comment, PR, provider assignment, reward, wallet, or payment state is claimed or mutated.
- This packet is current-source research and handoff evidence, not a claim that a maintainer has approved variant removal.
