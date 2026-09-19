# MentorsMind #1126 — KYC recovery source baseline

Source-readiness packet for `MentorsMind/MentorsMind-Contract#1126`, pinned to
`main@e90e16fc3a78122949ce63af7308320c59acb112`.

At intake the GitHub issue was OPEN, unassigned, had zero comments and no targeted open PR.
GrantFox supply marked it Unassigned/application-gated, so this packet does not modify upstream source.

## Issue premise

The issue asks for tests proving that a failed KYC update restores the previous KYC state using
`trigger_rollback`, `execute_with_recovery`, `RecoveryState`, and `RollbackProtector`, with
`cargo test -p mentorminds-kyc-registry` as the acceptance rail.

## Current-source correction

That behavior does not exist in the pinned source yet.

- KYC source: `contracts/kyc_registry/src/lib.rs`, blob
  `f00b859cf0be66f3b310d938db4cc5cefc59ac12`.
- KYC tests: `contracts/kyc_registry/src/test.rs`, blob
  `a824f4e0c348c89faf9a8fa7637397d26c2110d2`.
- Shared recovery helper: `contracts/shared/src/cross_contract_recovery.rs`, blob
  `598f1fe599fcdbe32add4262b1cc4b9c72a5021e`.
- Shared exports: `contracts/shared/src/lib.rs`, blob
  `23a2cd36472129ce1357c24c1f0bb3159ae39246`.

`execute_with_recovery` currently executes a closure and, on `Err(())`, returns
`Err(trigger_rollback(contract, action))`. `trigger_rollback` only constructs a
`CrossContractRecoveryState` marker with `rollback_required=true`; neither function snapshots,
restores, rewinds, or otherwise mutates contract storage.

The KYC `register_identity` caller is weaker still:

1. it wraps one `CrossPlatformIdentity` write in `execute_with_recovery`;
2. that closure always returns `Ok(())`, so its failure branch is unreachable;
3. the returned `Result` is discarded with `let _ = ...`;
4. the function then emits a success event and returns `true` unconditionally;
5. the wrapped write is a cross-platform identity record, not the `DataKey::Kyc` record whose
   previous value the issue acceptance explicitly says must be restored.

Repository search found no KYC recovery test and no other use of `CrossContractRecoveryState`
outside the helper/export itself.

## Type-contract mismatch

The issue says the tests must exercise `RecoveryState` and `RollbackProtector`.

`RollbackProtector` is a cross-contract-recovery type, but `RecoveryState` is exported from
`shared::failure_tracking`; the recovery helper exports `CrossContractRecoveryState` instead.
A test that merely constructs both names would not prove rollback semantics. This mismatch should
be resolved explicitly in the assigned implementation rather than hidden by a test-only patch.

## Disposition

**SOURCE_DRIFT_REPLAN / WAIT FOR MAINTAINER ASSIGNMENT.**

The issue cannot honestly be closed by adding tests only against the current helper. A test that
expects the previous KYC record to be restored would fail unless the implementation grows a real
recovery boundary; a test that only asserts the returned marker would weaken the published
acceptance contract.

## Assignment-time implementation plan

1. Decide the authoritative recovery target: the real `DataKey::Kyc(user)` record named by the
   issue, versus the separate `CrossPlatformIdentity` write currently wrapped by `register_identity`.
2. Add a real rollback mechanism. A safe design is an explicit snapshot/restore contract owned by
   the KYC call path (or a shared helper that accepts both the action closure and restoration
   closure), rather than treating a marker struct as restoration.
3. Do not swallow failure. A failed protected mutation must not emit the success event or return
   `true`; propagate a typed failure or otherwise make the top-level outcome fail closed.
4. Resolve the `RecoveryState` versus `CrossContractRecoveryState` naming/acceptance mismatch.
   Either exercise the type that actually governs this recovery path or update the issue contract
   with maintainer approval.
5. Keep the successful path unchanged: a valid protected update persists exactly once.

## Required predecessor-killing tests

- seed KYC state A; begin a protected update toward B; inject a failure after mutation; prove the
  authoritative KYC state is exactly A afterward;
- prove the same failed path emits no success event and cannot return success;
- prove a successful protected update persists B exactly once;
- directly validate `trigger_rollback` binds the current contract and action and reports rollback
  required / execution unsuccessful;
- if `RollbackProtector` remains part of the contract, exercise its actual lifecycle rather than
  constructing a dead value;
- distinguish the generic failure-tracking `RecoveryState` from `CrossContractRecoveryState`;
- retain the existing KYC lifecycle/privacy suite and run `cargo test -p mentorminds-kyc-registry`.

## Application-ready summary

A source-specific application can accurately say: current KYC imports the shared recovery API,
but the helper only returns a rollback marker and does not restore storage; the only KYC caller
wraps an always-successful identity write, discards the result, and unconditionally reports success.
The implementation therefore needs a real fail-closed KYC snapshot/restore boundary plus tests,
not tests alone. I can implement the bounded repair after maintainer assignment.

Implementation remains intentionally on hold until assignment.
