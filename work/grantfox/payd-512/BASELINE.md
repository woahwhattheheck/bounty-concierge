# PayD #512 source baseline

Pinned upstream main: `af5c348e83033ed3340e589b68e8554f0303060e`.

The vesting contract already emits upgrade events, but initialize, claim, and
clawback do not emit grant-lifecycle events. Current storage is one
`VestingConfig` per contract instance and has no `schedule_id`.

There is also no vesting-grant cancellation operation on current main.
`clawback()` revokes future vesting while preserving already vested rights;
the only cancel function is `cancel_upgrade()`, which already has its own
upgrade event.

Assigned implementation should preserve the existing storage layout and add
events only to real lifecycle transitions. The requested schedule identifier and
VestingCancelled transition need maintainer clarification rather than invented
storage/API semantics.

The backend indexer is already generic for vesting events. Contract tests should
verify the real Soroban event topic/data convention and keep existing upgrade
event behavior unchanged.

GrantFox was Unassigned with Apply enabled when checked. This is pre-assignment
research only; refresh assignment and source before upstream implementation.
