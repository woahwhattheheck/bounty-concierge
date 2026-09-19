# GrantFox baseline — Protocol-Guild/PayD #514

Operation: `GFOX2-20260919-112/R-source-census+application`  
Worker: ZZ-Kepler-Sol · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream: `Protocol-Guild/PayD@af5c348e83033ed3340e589b68e8554f0303060e`

## Provider / issue state

- GitHub issue: https://github.com/Protocol-Guild/PayD/issues/514
- GrantFox: https://contribute.grantfox.xyz/org/Protocol-Guild/repo/PayD/issue/514
- GitHub state: OPEN, unassigned.
- Labels: `contract`, `medium`, `Maybe Rewarded`, `GrantFox OSS`, `Third Campaign`.
- Existing application comments at census: 1.
- Fresh #514 PR search: no matching carrier surfaced.
- Reward amount, award, and payment are unverified.
- Upstream GitHub App permission exposed to this worker is pull-only; implementation remains fenced on provider/maintainer assignment.

## Current-source proof

### Contract

`contracts/bulk_payment/src/lib.rs` blob: `84eb1a15b6814b47564a595d2db2b8ec8dd812d2`

The issue is still live at the pinned head:

- `DataKey` contains only `Admin`, `BatchCount`, `Batch(u64)`, and `Sequence`; there is no paused state.
- `ContractError` has no paused/error variant.
- There are no pause/unpause events.
- Both money-moving entrypoints, `execute_batch` and `execute_batch_partial`, lack an emergency pause check.
- Both methods call `sender.require_auth()` and then `check_and_advance_sequence()` before validation or token movement. A pause guard should execute before sequence mutation so a rejected paused call cannot consume replay state.

### Existing admin seam

`contracts/common/src/lib.rs` blob: `b048e006b2da3c25d567488fdebf3d91055747e3`

`common::require_admin` already reads the stored admin and calls `require_auth()`. The bulk contract's existing `set_admin` reuses it. Pause/unpause should reuse the same helper rather than introduce another authorization convention.

### Existing test seam

`contracts/bulk_payment/src/test.rs` blob: `7940c3c7bca049d2571c87ddbde892fc34b7c111`

The current suite already provides:

- `env.mock_all_auths()` setup;
- token minting/balance assertions;
- batch/sequence assertions;
- a reusable event-inspection helper;
- both all-or-nothing and partial-batch cases.

That makes the issue a focused contract/state transition addition rather than a new test harness.

## Post-assignment implementation plan

1. Add an instance-storage paused flag initialized to `false`, plus admin-authenticated `pause` and `unpause` functions using `common::require_admin`.
2. Add one explicit paused contract error and enforce it at the top of both payment entrypoints, before sequence advancement and before any token operation.
3. Emit typed pause/unpause events. Add a read-only pause-state getter if useful to clients/tests.
4. Extend the existing Soroban tests for:
   - admin pause and unpause;
   - unauthorized pause/unpause rejection;
   - both batch modes rejected while paused;
   - sequence and balances unchanged after paused rejection;
   - successful payment after unpause;
   - pause/unpause event emission.
5. Run focused bulk-payment contract tests and the repository-required workspace checks; report exact commands and failures without claiming hosted green unless observed.

Out of scope: timelock/governance, unrelated payment/refactor work, reward/payment assumptions.

## Source-specific application draft

> Applying for #514 after checking current main at `af5c348e83033ed3340e589b68e8554f0303060e`.
>
> I traced the current contract before proposing changes. `contracts/bulk_payment/src/lib.rs` has two money-moving entrypoints (`execute_batch` and `execute_batch_partial`), `DataKey` has no paused state, and neither entrypoint checks an emergency guard. The shared `common::require_admin` helper is already used by `set_admin`, and the existing test module already has auth mocking plus event inspection helpers.
>
> Approach:
> 1. Add a single instance-storage paused flag initialized false, plus admin-authenticated `pause` / `unpause` entrypoints using the existing shared admin helper.
> 2. Add one explicit paused contract error and check it before sequence mutation or token movement in both payment entrypoints, so rejected paused calls cannot consume sequence numbers or move funds.
> 3. Emit typed pause/unpause events and expose a read-only pause-state getter if needed for clients/tests.
> 4. Extend the existing Soroban tests for authorized state transitions, unauthorized admin calls, both batch modes while paused, sequence/balance non-mutation on rejection, unpause recovery, and event emission; then run the focused contract tests and workspace-required checks.
>
> I’ll keep unrelated rate/payment refactors out of scope. Happy to begin after assignment.
