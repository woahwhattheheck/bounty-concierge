# Ajo #851 insurance-solvency source baseline

Source-readiness packet for `Ajo-contrib/soroban-ajo#851`, pinned to
`master@1b87ea7344ae3d871e54abff05eabe5113bd2956`.

At the 2026-09-19 census, GitHub showed the issue open and unassigned with no
Development carrier. GrantFox showed **Unassigned**, zero comments, and an active
"Apply to this issue" route. This packet is pre-assignment research only and does
not modify the upstream contract.

## Current lifecycle

The issue is directionally correct that pending liabilities are not reserved, but
the current lifecycle narrows the simplest race described in the issue:

1. `file_claim()` creates `ClaimStatus::Pending`, stores the full amount, and
   increments only `InsurancePool.pending_claims_count`. It does **not** reserve
   the amount or call the solvency check.
2. `process_claim(..., approved=true)` requires the claim still be Pending,
   calls `check_pool_solvency()`, checks the current pool balance, immediately
   pays the claimant, and changes the claim directly to `Paid`.
3. No current source reference writes `ClaimStatus::Approved`; there is no
   separate approved-but-unpaid reserve state on this pin.
4. `auto_process_claim()` eventually delegates valid Pending claims to
   `process_claim()`.

Because payout re-checks solvency, multiple **fresh** pending claims cannot simply
all rely on the same pre-approval total: a prior payout becomes Paid before the
next sequential `process_claim()` call.

## Residual cap bypass

The real accounting boundary is still unsafe. `check_pool_solvency()` defines
the active seven-day total by scanning claims whose:

- `status == Paid`, and
- **`claim.created_at > now - EPOCH_DURATION`**.

That timestamp is filing time, not payout/settlement time. Therefore an unresolved
Pending claim can age out of the accounting window before it is paid.

Concrete sequence:

1. Pool balance is `B`; nominal epoch cap is 5% of the current balance.
2. File several Pending claims while they are individually valid business claims.
   Their amounts are not reserved.
3. Leave them Pending for more than seven days.
4. Process the first aged claim. It contributes its own amount through the
   `+ claim_amount` term and may pass.
5. The claim becomes Paid, but its `created_at` is already older than the
   seven-day cutoff.
6. Process the next aged claim. The previous payout is still omitted from
   `epoch_claims` because the scan keys inclusion to old filing time.
7. Repeat. Each aged payout can see prior aged payouts as zero even though they
   are being settled in the same live payout period.

The struct already exposes `last_epoch_reset`, `epoch_claimed_amount`, and
`epoch_duration`, but `check_pool_solvency()` does not use them and the current
path does not update `epoch_claimed_amount`. `pending_claims_count` records
cardinality only, not reserved value.

A second lifecycle inconsistency exists in the high-fraud early-return branch of
`auto_process_claim()`: it writes the claim to `Rejected` directly and returns
without the normal `process_claim(false)` path that decrements
`pending_claims_count`. That counter is not currently a value reserve, but an
assignment-time repair should avoid making it a stale authority input.

## Assignment-time invariant

Do not define solvency from mutable claim status plus `created_at` scans. The
contract needs one explicit accounting invariant:

> For a token's active insurance epoch, settled payouts plus live reserved
> liabilities must never exceed that epoch's claimable budget.

A bounded implementation should keep O(1) accounting per pool/epoch rather than
make the hot path scan every historical claim. The existing epoch fields are the
natural starting point, but any persisted-shape change must respect the
repository's storage-schema/migration contract.

Recommended semantics:

- **epoch rollover:** before file/process operations, roll the pool epoch from
  `last_epoch_reset` using `epoch_duration`; clear only the completed epoch's
  settled/reserved counters under an explicit rule.
- **file:** after validation, reserve the claim amount atomically against
  `settled + reserved + new_amount <= epoch_budget`; reject before storing a
  claim that would exceed the cap.
- **reject:** release that claim's reservation exactly once.
- **pay:** convert exactly that claim's reservation into settled usage, then
  execute payout without double counting. A failed transfer must roll back the
  reservation/status/pool mutation with Soroban transaction atomicity.
- **stale Pending across epochs:** define explicitly whether a reservation remains
  charged to its filing epoch until resolution, is re-admitted against the new
  epoch, or must expire/re-file. Do not silently free it merely because wall time
  advanced.
- **idempotence:** non-Pending claims must never release or settle the same amount
  twice.

If adding an amount field to `InsurancePool` would violate the persisted-v1
compatibility policy, use a separately keyed additive reservation record rather
than silently changing the serialized struct without a migration.

## Required hostile regressions

1. Two or more fresh Pending claims whose combined value exceeds the cap: the
   boundary claim is rejected/reservation fails.
2. Several Pending claims aged beyond seven days, then paid sequentially: prior
   payouts remain charged and the cap cannot be bypassed by old `created_at`.
3. Exact-cap boundary succeeds; one stroop over fails.
4. Pending -> Rejected releases its reservation exactly once.
5. Pending -> Paid converts reservation to settled usage exactly once.
6. Re-processing Paid/Rejected claims fails without changing accounting.
7. Token transfer failure leaves claim status and pool accounting unchanged.
8. Epoch rollover with unresolved Pending liability follows the chosen explicit
   policy and cannot make the liability disappear implicitly.
9. The high-fraud auto-reject path keeps pending count/reservation accounting
   coherent.
10. Two token pools remain accounting-isolated.

## Pinned evidence

- `contracts/ajo/src/insurance.rs` blob
  `cf1864c1243047d07a2e66952b936dc1bbac2677`
- `contracts/ajo/src/types.rs` blob
  `207c38fb98769d166f1a93f22f05240241345faa`
- `contracts/ajo/src/storage.rs` blob
  `2434706525a71819787ea17f4421ff64102b3505`
- `contracts/ajo/tests/security_tests.rs` blob
  `ad7883958c16774a3db50baeeaef7840c2f307a5`
- `contracts/ajo/tests/security_audit_tests.rs` blob
  `9033f536ece503e2620ecb890624d69e3bec459a`

No dedicated #851 regression exists on the pinned source. Implementation remains
held until provider/maintainer assignment; the next immediate action is a
source-specific GrantFox application that cites the lifecycle correction and
aged-Pending bypass above.
