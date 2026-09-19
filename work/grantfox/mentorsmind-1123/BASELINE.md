# GrantFox baseline — MentorsMind/MentorsMind-Contract #1123

Operation: `FORGE-GFOX-MM-1123-R-CINDER17-20260919`  
Worker: ZZ-Cinder-17 · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `e90e16fc3a78122949ce63af7308320c59acb112`

## Provider state

- GitHub issue: https://github.com/MentorsMind/MentorsMind-Contract/issues/1123
- GrantFox: https://contribute.grantfox.xyz/org/MentorsMind/repo/MentorsMind-Contract/issue/1123
- GitHub: OPEN, unassigned, 0 comments
- GrantFox: Unassigned, Apply enabled, direct GitHub-comment application, 1 application per user
- Matching #1123 PR surfaced by connector search: none
- Upstream connector permission: pull=true, push=false
- No reward amount, assignment, payment, or award is asserted by this packet.

## Current-source contract

The requested risk-monitoring seam exists, but the issue's implementation guideline is stale relative to current main.

Pinned blobs:
- `contracts/session_registry/src/lib.rs`: `4927c25d8267ba1e5dfcfeef277a86b4ba902f02`
- `contracts/shared/src/metadata_validation.rs`: `7421b6206ef32505965ded047d87525de1691df6`

The issue says `monitor_metadata_manipulation` takes an entity address plus old/new metadata hashes and proposes `DataKey::PreviousMetadata(session_id)`. Current shared code instead defines:

```rust
pub fn monitor_metadata_manipulation(
    update_frequency: u32,
    unverified_changes: u32,
) -> MetadataMonitoringRecord
```

It computes a 0–100 manipulation level from update frequency and unverified-change count; `misinformation_detected` becomes true at level >= 50. There is no `PreviousMetadata` key on current main and no old/new-hash parameter in this helper.

Current session registry already provides a separate `monitor_metadata_changes(session_id, update_frequency, unverified_changes)` endpoint. It:
- verifies the session exists;
- calls the shared helper;
- stores the record under `DataKey::SessionMetadataMonitoring(session_id)`;
- emits `(metamon, misinfo, session_id)` when `misinformation_detected` is true.

But the actual Symbol-keyed metadata setter directly writes `DataKey::SessionMetadata(session_id)` and never invokes this monitoring path before commit.

## Additional source ambiguity

Current `session_registry/src/lib.rs` contains two textual `update_session_metadata` definitions with different signatures/storage schemes:
- `(session_id: u64, tags: Vec<String>)` using tuple key `(SessMeta, session_id)`;
- `(session_id: Symbol, tags: Vec<String>)` using `DataKey::SessionMetadata`.

This packet does not infer compile status from that alone. It does mean an assigned patch must identify the canonical exported setter before adding monitoring, rather than modifying whichever textual occurrence is easiest.

The contract-level comment says the platform backend is the only caller allowed to register/update, while the Symbol-keyed metadata setter shown on current main does not itself call `require_backend` / `require_auth`. That is a separate security seam to reconcile with maintainers rather than silently expanding #1123.

Fresh repository search surfaced no direct tests for `update_session_metadata`, `monitor_metadata_changes`, or `SessionMetadataMonitoring`.

## Assignment-gated implementation plan

After provider/maintainer assignment:

1. Confirm the canonical metadata setter and whether the duplicate u64/Symbol surface is intentional, generated drift, or dead code.
2. Reuse the live `MetadataMonitoringRecord` / `SessionMetadataMonitoring` / `metamon:misinfo` semantics unless maintainers explicitly want the helper redesigned around metadata hashes.
3. If keeping the current numeric helper, add deterministic persistent update-history inputs (frequency/unverified-change signal) and compute monitoring **before** writing the new metadata.
4. Persist the monitoring record for auditability. If the resulting level crosses the existing risk threshold, emit the alert and abort before metadata mutation.
5. If the maintainer requires old/new hash comparison exactly as the issue prose says, extend or add a shared helper intentionally and update storage/schema snapshots; do not pretend the current helper already accepts those arguments.
6. Clarify whether the metadata setter must also enforce the contract's backend-only update invariant; cover unauthorized mutation if included.
7. Add Soroban tests for safe update, rapid successive updates that trigger monitoring, high-risk blocked update preserving prior metadata, persisted monitor record, event emission, and the canonical setter only.
8. Run the session-registry package tests plus repository-required formatting/lint/storage-snapshot checks.

## Provider application draft

> Applying for #1123 after tracing current `main@e90e16fc3a78122949ce63af7308320c59acb112`. The intended monitoring primitive is present, but its live API differs from the issue guideline: `monitor_metadata_manipulation` currently accepts `(update_frequency, unverified_changes)`, not entity + old/new metadata hashes. The session registry already persists its result under `SessionMetadataMonitoring` and emits a `metamon/misinfo` event, while the real metadata setter bypasses that path entirely. I also found two textual metadata-setter signatures and no `PreviousMetadata` key, so I would resolve that current-source seam first rather than invent a hash API that does not exist.
>
> After assignment I will wire monitoring before the canonical metadata write, preserve a prior value on high-risk rejection, persist the monitoring record, prove the alert event, and add rapid-update + blocked-update tests. If you want the hash-based design in the issue text rather than the current frequency/change-count model, I will make that shared-helper/storage change explicit and update the relevant schema snapshots instead of hiding it inside the setter.

## Authority fence

This is source/readiness evidence only. No upstream source, provider assignment, application, reward, wallet, payment, or adjudication state is mutated.
