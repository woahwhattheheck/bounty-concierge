# MentorsMind Contract #1128 source baseline

Source-readiness packet for `MentorsMind/MentorsMind-Contract#1128`, pinned to
`main@e90e16fc3a78122949ce63af7308320c59acb112`.

Live GrantFox readback on 2026-09-19 showed **Unassigned**, **Apply to this issue**,
zero comments, and one application allowed per user. GitHub also shows the issue open
with no assignee and zero comments. This packet does not change upstream source and
does not claim provider assignment, reward amount, or payout authority.

## Source reality vs. issue text

The current source does not match several assumptions in #1128:

- The issue says both `record_mev_monitoring` and `compute_mev_redistribution` are
  imported by the oracle. Current `contracts/oracle/src/lib.rs` imports
  `record_mev_monitoring`, but **does not import `compute_mev_redistribution`**.
- The issue asks `get_twap` (or the TWAP computation path) to record the **caller**.
  Current oracle source has **no `get_twap` entrypoint**. The active read path is
  `get_price(env, asset) -> (i128, u64)`, which has no caller `Address` parameter.
- Repository docs and integration tests still reference `get_twap(asset)`, so the
  docs/test contract has drifted from current `lib.rs`.
- `record_mev_monitoring` requires a protocol symbol, caller address,
  `MevProtectionFlag`, and `FairValueExtractionRecord`. It returns a monitoring
  record and emits an event, but **does not persist the returned record**.
- `compute_mev_redistribution` accepts `transaction_volume` plus a
  `MevProtectionFlag`; it does not accept price deviation. The shared MEV flag is
  currently derived from recent interaction count (>=3 arbitrage, >=5 sandwich), not
  from TWAP deviation.
- A TWAP/price **read has no authoritative transaction volume** in the current API.
  Passing price, deviation, or another invented quantity as `transaction_volume`
  would silently change the helper's economics.

These are contract-design choices, not mechanical wiring. An implementation should
not fabricate caller identity, transaction volume, or a deviation-to-risk mapping.

## Existing MEV path that must not be conflated with read monitoring

`submit_price(env, feeder, ...)` already has an authenticated actor. It increments a
temporary per-feeder/per-ledger interaction count, derives a shared
`MevProtectionFlag`, and blocks when `enforce_protocol_isolation` sees risk >= 75.
That is **submission-time feeder protection**. #1128 asks for **read-time price/TWAP
monitoring**. Reusing the feeder as the read caller would be incorrect.

The current read path also mutates persistent TWAP state after freshness,
reorg-safety, feeder-count, median, and outlier validation. Monitoring should never be
written for a read that fails those existing guards.

## Safe assignment-time decision points

Before source implementation, the maintainer/provider should resolve these points:

1. **Reader identity / ABI** — either approve a new caller-bearing monitored read
   entrypoint (for example a separate `get_price_for(caller, asset)`) or explicitly
   redefine the acceptance criterion so the existing callerless read stays callerless.
   Do not silently break `get_price(env, asset)` consumers.
2. **Authentication policy** — if the monitored read accepts a caller, decide whether
   that address must `require_auth()` or is telemetry-only. Do not invent this.
3. **Read-time MEV score** — define how TWAP deviation becomes a
   `MevProtectionFlag`, if that is the desired model. The existing interaction-count
   detector is not a price-deviation detector.
4. **Redistribution input** — define the economic quantity represented by
   `transaction_volume` for a read, or introduce an explicit read-monitoring helper
   rather than smuggling price/deviation into a volume parameter.
5. **Storage key / cardinality** — persist only the issue-required flagged records
   under a bounded temporary key. A caller + asset + ledger-sequence shape is a
   reasonable candidate only after the caller contract is approved; repeated reads in
   one ledger should not create unbounded keys.
6. **TTL** — reuse the repository's existing
   `shared::ttl_utils::TTLManager::extend_temporary` policy (safety-margin threshold,
   one-day bump) instead of inventing a second TTL policy.
7. **Aggregation policy** — `get_aggregated_price` internally calls `get_price`.
   Decide whether one external aggregated read should create one monitoring record,
   none, or separate primary-read telemetry. Avoid accidental double recording.

## Regression matrix after clarification + assignment

At minimum:

- existing successful price reads retain freshness/reorg/min-feeder/outlier semantics;
- no monitoring record/event is produced when the read fails before a price exists;
- below-threshold read produces no flagged record;
- exact-threshold and above-threshold behavior is explicit and tested;
- a flagged read stores the exact approved caller/asset/flag/extraction fields;
- the temporary record receives the shared TTL policy and can expire;
- repeated same-ledger flagged reads obey the chosen bounded key semantics;
- two callers do not overwrite each other unless the approved key deliberately says so;
- aggregation does not accidentally double-record;
- existing submission-time feeder MEV isolation remains unchanged;
- docs and tests are reconciled with the actual `get_price` / `get_twap` API.

Run `cargo test -p mentorminds-oracle` plus the repository oracle manipulation / flash
loan integration tests after the API mismatch is reconciled.

## Application-ready note

Suggested provider comment:

> Hi maintainers, I audited #1128 against current
> `main@e90e16fc3a78122949ce63af7308320c59acb112`. The issue is live, but current
> source differs from the description: the oracle imports
> `record_mev_monitoring` but not `compute_mev_redistribution`; the active read is
> callerless `get_price(env, asset)`; current `lib.rs` has no `get_twap` even
> though docs/tests still call it; and the shared redistribution helper expects a
> transaction volume while the issue describes price deviation. I can implement the
> monitoring + temporary-TTL tests after assignment, but I want to preserve the public
> read ABI and avoid inventing caller/volume semantics. Please assign me and confirm
> whether you want a new caller-bearing monitored read (and its auth policy), and what
> economic value should feed redistribution on a read. I will reuse the existing
> `TTLManager::extend_temporary` policy and keep submission-time MEV isolation
> unchanged.

Disposition: **WAIT_FOR_PROVIDER_ASSIGNMENT_AND_SPEC_CLARIFICATION**.
