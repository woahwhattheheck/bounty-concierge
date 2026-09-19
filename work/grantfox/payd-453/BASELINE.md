# PayD #453 — bounded sender-enumeration source baseline

Source-readiness packet for `Protocol-Guild/PayD#453`, pinned to
`main@af5c348e83033ed3340e589b68e8554f0303060e`.

At the source fence the GitHub issue was OPEN and the connector found no open PR matching #453.
The linked GitHub installation is pull-only for upstream (`pull=true`, `push=false`).
A live GrantFox fleet census at 2026-09-19 18:44:04 EDT reported **Unassigned**,
`Apply to this issue`, one application per user / direct GitHub comment, and two generic
applications. This packet does not submit an application or modify upstream source.

Canonical fleet observation:
https://tokenjunkielabs.slack.com/archives/C0BVANHNB26/p1789857844181929

## Issue contract

The issue asks both Soroban contracts to expose sender-scoped enumeration:

- `bulk_payment`: batch IDs (or batches) for one sender;
- `cross_asset_payment`: payment IDs for one sender;
- empty, single-item, and multiple-item unit tests;
- existing contract tests remain green;
- reads must respect storage/read limits.

Backend pagination/indexer changes are explicitly out of scope.

## Current-source map

### bulk_payment

- Runtime: `contracts/bulk_payment/src/lib.rs`, blob
  `84eb1a15b6814b47564a595d2db2b8ec8dd812d2`.
- Tests: `contracts/bulk_payment/src/test.rs`, blob
  `7940c3c7bca049d2571c87ddbde892fc34b7c111`.
- Cargo: `contracts/bulk_payment/Cargo.toml`, blob
  `419d7e39b09fe79b047c3fd1e6af73110f0cd63f`.

`BatchRecord` already retains `sender: Address`, but the only index is global
`DataKey::BatchCount` plus `DataKey::Batch(u64)`. Both all-or-nothing and partial execution
allocate one new global batch ID and write one `BatchRecord` into **instance storage**.
The public read surface is only `get_batch(batch_id)` and `get_batch_count()`.

There is no sender count, sender ordinal/index key, page key, or sender-list API.

### cross_asset_payment

- Runtime: `contracts/cross_asset_payment/src/lib.rs`, blob
  `c5715147e06fbdfdab70115d68e7a8e259f34791`.
- Tests: `contracts/cross_asset_payment/src/test.rs`, blob
  `e4f28753bde783f3d22b5d942af81ee67e91a28a`.
- Cargo: `contracts/cross_asset_payment/Cargo.toml`, blob
  `183ce6e0d748917f8fdde206b5d8cd906da1686b`.

`PaymentRecord` retains `from: Address`. `initiate_payment` allocates a global
`PaymentCount` ID and writes `DataKey::Payment(id)` into **persistent storage**.
Each payment record is extended to `PAYMENT_TTL_LEDGERS = 518_400` (documented in source as
approximately 30 days at 5 seconds/ledger). The public read surface is only
`get_payment(payment_id)` and `get_payment_count()`.

There is no sender count/index/list API.

The existing backend `ContractEventIndexer` separately polls Soroban events and stores them in
PostgreSQL. It is not a contract-state enumeration primitive and does not satisfy #453's
contract-only acceptance requirement.

## Why a global scan or monolithic Vec is not acceptable

A getter that loops `1..=BatchCount` / `1..=PaymentCount` and filters by sender is O(global
history), reads unrelated users' records, and becomes more expensive as the contract grows.

Likewise, a single unbounded `Vec<u64>` per sender makes each append/read scale with that
sender's full history and, for bulk payment, would further enlarge already-global instance state.
The issue explicitly calls out storage/read limits; either approach defeats that requirement.

## Assignment-time bounded contract

Use an append-only **per-sender ordinal index**, not a global scan and not one unbounded sender
vector. The exact storage class and numeric page cap should be confirmed with maintainers, but the
observable contract should have these properties:

1. Each successful new batch/payment increments a sender-local count and binds that sender-local
   ordinal to the already-existing global ID. Index writes happen in the same contract invocation
   as the canonical record write, so a reverted invocation cannot leave an orphan index entry.
2. Expose a bounded ID read such as
   `list_batch_ids(sender, start, limit)` / `list_payment_ids(sender, start, limit)`.
   `limit` must be capped by a small documented contract constant; runtime work is O(limit),
   independent of global record count.
3. `start >= sender_count` returns an empty vector. The contract must explicitly define zero-limit
   behavior rather than relying on accidental loop behavior.
4. The list returns **IDs**, not full records. Existing `get_batch` / `get_payment` remain the
   canonical record readers, avoiding a second unbounded read surface.
5. Sender isolation is structural: Alice's ordinal keys can never enumerate Bob's IDs.
6. Preserve existing global IDs/counts and get-by-ID behavior. No renumbering or incompatible
   rewrite of `BatchRecord` / `PaymentRecord` is needed for the forward path.

A concrete key shape could be sender count + sender ordinal → global ID (for example
`SenderBatchCount(Address)`, `SenderBatchAt(Address,u64)`, with payment equivalents). This is
preferable to a monolithic vector because each append is constant-size and list work is bounded.

## Two compatibility questions that must not be hidden

### 1. Cross-asset TTL semantics

Payment records are intentionally finite-lived today. An index that outlives
`DataKey::Payment(id)` can return an ID whose canonical record has expired; an index that expires
earlier loses discoverability while a record still exists.

The assigned implementation must define sender enumeration as **retained payment IDs** and align
index-entry lifetime with the canonical payment retention policy, or obtain explicit maintainer
approval for a different historical-ID contract. Do not silently turn a ~30-day payment store into
permanent history merely to satisfy listing.

### 2. Pre-upgrade records

A newly introduced sender index naturally captures only writes after the upgrade. If a deployed
contract already has batches/payments, claiming the new API returns "all" sender IDs would be false
unless migration/backfill semantics are defined.

Before implementation, confirm whether #453 acceptance is forward-only for a fresh/test deployment
or must cover existing on-chain state. If migration is required, it needs its own bounded strategy;
a one-shot scan of every global ID is exactly the unbounded behavior this issue is trying to avoid.

## Required regression matrix

Minimum issue acceptance:

- empty sender → empty IDs;
- one sender / one record → exactly that global ID;
- one sender / several records → stable creation order;
- existing contract suites remain green.

Predecessor-killing coverage:

- two senders interleaved → each lists only its own IDs;
- bounded slices (`start`, `limit`) compose to the full sender sequence with no duplicates/gaps;
- start at/end/beyond count is deterministic;
- request above the contract limit fails or clamps exactly as documented;
- both `execute_batch` and `execute_batch_partial` index one successful batch;
- rejected/reverted batch/payment creates no sender-index entry;
- payment status update/cancel does not duplicate its sender index entry;
- global `get_*_count` and `get_*(id)` semantics remain unchanged;
- cross-asset TTL/index lifetime behavior is tested according to the maintainer-approved retention
  contract;
- if pre-upgrade state is in scope, seed legacy records and prove the approved migration path is
  bounded and complete.

## Application-ready summary

Current source confirms #453's gap: both records already retain sender identity, but both contracts
only expose global ID lookup/count surfaces. I would add a constant-work per-sender ordinal index
and bounded ID listing APIs, preserve existing global IDs/getters, and test empty/single/multiple,
cross-sender isolation, page boundaries, rollback/no-orphan behavior, and the cross-asset payment
TTL contract. I would also clarify whether pre-upgrade records must be discoverable before claiming
"all" sender records.

Implementation remains intentionally on hold until real GrantFox/maintainer assignment.
