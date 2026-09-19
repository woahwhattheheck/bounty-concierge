# GrantFox source baseline — Stellar-MarkeyPay/Stellar-MarketPay #175

Operation: `GFOX2-20260919-101/R-pagination-current-main-map`  
Worker: ZZ-Sol-Rook · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `42250890ecb5b76228675167f47452e54fb28977`

## Canonical issue and authority

- GitHub: https://github.com/Stellar-MarkeyPay/Stellar-MarketPay/issues/175
- GrantFox: https://contribute.grantfox.xyz/org/Stellar-MarkeyPay/repo/Stellar-MarketPay/issue/175
- Provider state at census: Unassigned, one visible prior application, `Maybe Rewarded` / `GrantFox OSS`
- No fixed award, payment, or assignment is established by this packet.
- This is a pre-assignment source map only; no upstream source or provider state was mutated.

## Current-main pagination reality

The issue reads like a broad offset-to-cursor migration, but current main has already partially migrated. The remaining work is narrower and more specific.

### Notifications: cursor exists but is not tie-safe

Pinned service blob: `backend/src/services/notificationService.js@e25a11332a26e0139d928486feaa6cc45acab39d`.

The service orders rows by:

`created_at DESC, id DESC`

but when a cursor is supplied it filters only:

`created_at < cursor`

and emits `nextCursor` as only the final row's `created_at`.

That means multiple rows sharing the page-boundary timestamp can be skipped on the next page even though `id` is the tie-breaker in the ORDER BY. The frontend already treats the cursor opaquely and appends pages, so a composite opaque `(created_at,id)` cursor can be introduced without a broad UI rewrite.

Frontend notification page blob: `540e9ff933b574692e361025d983758b033a280d`.

### Jobs: keyset exists but omits the leading sort key

Pinned service blob: `backend/src/services/jobService.js@af5466811135687dc028c8382093ed87b43a45df`.

Jobs already encode `(createdAt,id)` and filter lexicographically on those fields. The query, however, orders by:

1. active-boost bucket;
2. `created_at DESC`;
3. `id DESC`.

The cursor does not encode the leading boost bucket. Crossing boosted/unboosted rows, or a boost status changing between page requests, can therefore make continuation inconsistent with the ORDER BY.

The REST route already marks legacy `page` as deprecated and cursor pagination as canonical. Frontend `fetchJobs` consumes `nextCursor`, and the jobs index follows cursors to reconstruct requested pages while de-duplicating IDs. The repair should align the cursor with every stable ORDER BY key, or define a snapshot/nonvolatile rank for boost ordering, not reintroduce offset paging.

Pinned route blob: `7aced8530899513aaed3b505729411609fc6e099`.  
Pinned frontend API blob: `a5edc12a9f8935b7f1de6ea07eb149f09422d4df`.

### Applications: still fully materialized/unpaginated

Pinned service blob: `backend/src/services/applicationService.js@1749c23373c02c97eadc1673c4e96369f55ea961`.

`getApplicationsForJob` returns the entire application set ordered `a.created_at ASC`, then optionally filters tiers in memory. `getApplicationsForFreelancer` likewise returns the complete set.

REST `fetchApplications(jobId,tier)` expects a raw `Application[]`; therefore moving to a paged response needs a compatibility/deprecation contract.

Pinned GraphQL pagination blob: `backend/src/graphql/pagination.js@65d65ce6d2f95329f11d637d1ec1d8846f61fd00`.

The GraphQL helper explicitly documents that service layers without keyset paging receive offset encoded inside an opaque cursor. `connectionFromArray` materializes the full collection and slices it, so applications remain offset-backed even when the public cursor is opaque.

## Assignment-ready acceptance map

After assignment, the remaining work should be:

1. notifications: opaque composite `(created_at,id)` cursor and matching lexicographic predicate;
2. jobs: cursor/order contract that includes the active boost rank or otherwise freezes that volatile sort dimension;
3. applications: DB-level keyset pagination using a deterministic tie-breaker, while preserving a documented REST compatibility/deprecation period and migrating GraphQL away from full-array slicing;
4. tests with tied timestamps, concurrent inserts between pages, mixed boost buckets, and boost-state changes proving no skipped or duplicated rows;
5. retain existing cursor-based frontend paths where they already work instead of rewriting them.

Fresh code search found no focused notification/job service tests covering these cursor-stability boundaries.
