# MentorsMind #1126 — KYC rollback/recovery source baseline

**Work order:** FORGE-GFOX-MM-1126  
**Worker:** ZZ-Sol-Marmot-26  
**Model:** GPT-5.6 Sol  
**Observed:** 2026-09-19  
**Disposition:** SOURCE_ACCEPTANCE_MISMATCH / implementation remains provider-assignment-gated

## Pinned upstream state

- Repository: `MentorsMind/MentorsMind-Contract`
- Default branch: `main`
- Main SHA: `e90e16fc3a78122949ce63af7308320c59acb112`
- Issue: `#1126 Add execute_with_recovery and trigger_rollback tests to lib.rs`
- GitHub issue state at observation: OPEN, unassigned, 0 comments
- No PR matching `1126` was found by repository PR search.
- Public GrantFox listing: `https://contribute.grantfox.xyz/org/MentorsMind/repo/MentorsMind-Contract/issue/1126`
- GrantFox public state at observation: **Unassigned**, `Apply to this issue` visible, direct GitHub-comment application flow, 0 visible comments. The page did not expose a verified reward amount.

## Exact source pins

| Surface | Blob SHA | Current behavior relevant to #1126 |
|---|---|---|
| `contracts/shared/src/cross_contract_recovery.rs` | `598f1fe599fcdbe32add4262b1cc4b9c72a5021e` | Defines `CrossContractRecoveryState`, `RollbackProtector`, `trigger_rollback`, and `execute_with_recovery`. |
| `contracts/kyc_registry/src/lib.rs` | `f00b859cf0be66f3b310d938db4cc5cefc59ac12` | Imports the recovery symbols. KYC setters mutate storage directly. `register_identity` uses `execute_with_recovery` around one identity write. |
| `contracts/kyc_registry/src/test.rs` | `a824f4e0c348c89faf9a8fa7637397d26c2110d2` | Existing lifecycle/privacy tests; no rollback/recovery regression for #1126. |
| `contracts/shared/src/failure_tracking.rs` | `1fd8a7e6347abd8af9bca83105d85ce1621e4f9f` | Defines the imported `RecoveryState` enum for escrow failure tracking. |
| `contracts/kyc_registry/Cargo.toml` | `be80c5dbaeb6cf7389949f42ee6a372280a31e03` | Package name is `mentorminds-kyc-registry`; requested test command is source-compatible. |

## Source/acceptance mismatch

The issue asks for a test proving that a failed KYC update is restored by `trigger_rollback` / `execute_with_recovery`. Current helper code does not itself implement storage rollback:

1. `trigger_rollback(contract, action)` only constructs and returns a `CrossContractRecoveryState` with `rollback_required = true` and `execution_successful = false`.
2. `execute_with_recovery` calls the supplied closure. On `Err(())`, it returns the marker from `trigger_rollback`. It receives no `Env`, snapshot, undo callback, storage keys, or restoration function.
3. `RollbackProtector` is currently only a data structure with `snapshot_id` and `is_active`. Repository code search found no behavioral implementation or construction site beyond definition/import surfaces.
4. The issue names `RecoveryState`, but the recovery helper exports `CrossContractRecoveryState`. The `RecoveryState` imported by KYC is the unrelated escrow/failure-tracking enum (`Retrying`, `AwaitingManualRecovery`, `Escalated`, `Recovered`).
5. `set_kyc_level`, `renew_kyc`, and `revoke_kyc` do not use `execute_with_recovery`. The only current KYC use is `register_identity`; its closure always returns `Ok(())`, the wrapper result is ignored, and the function publishes a success event and returns `true`.

Therefore a test-only PR must not claim that the helper restores prior KYC storage unless that behavior is demonstrated by the host/runtime. A unit test that merely inspects the returned rollback marker would satisfy only marker semantics, not the issue's state-restoration acceptance criterion.

## Assigned-seat implementation contract

Before changing product code, re-pin upstream `main`, re-check issue/PR/provider assignment, and run a failing behavioral probe that makes the distinction explicit:

1. Seed a real KYC record with a known level, expiry, and provider hash.
2. Execute a recovery-wrapped operation that mutates a relevant record and then deliberately returns `Err(())`.
3. Assert the returned error is the expected `CrossContractRecoveryState`: correct contract, correct action, `rollback_required=true`, `execution_successful=false`.
4. Read the KYC record back after the wrapper returns. This is the decisive test. If the mutation remains, the existing helper does **not** meet the stated rollback acceptance criterion and product/helper work is required before a green recovery test is truthful.
5. Add the success control: a wrapped `Ok` path persists the intended mutation.
6. Exercise `RollbackProtector` only through a real snapshot/restore seam if such a seam is added; constructing the struct solely to tick coverage is not evidence of protection.
7. Resolve the `RecoveryState` naming mismatch explicitly. Prefer testing `CrossContractRecoveryState` for this helper unless maintainers confirm that escrow `RecoveryState` is intentionally part of #1126.
8. Run `cargo test -p mentorminds-kyc-registry`; include the exact command and failure/pass evidence in the upstream PR.

## If restoration is genuinely required

The current generic helper cannot restore arbitrary Soroban state by itself. A compatible design needs an explicit rollback mechanism (for example, a caller-supplied restore/compensation closure or a concrete snapshot/restore abstraction tied to the affected storage keys) and tests must prove partial writes are undone. Do not hide this gap with a test that only checks the returned marker.

## Safety / custody fence

This packet is source analysis only. It does not claim GrantFox assignment, award, payment, or upstream implementation authority. Upstream code changes should begin only after the provider/maintainer assignment fence is satisfied and the current source is revalidated.
