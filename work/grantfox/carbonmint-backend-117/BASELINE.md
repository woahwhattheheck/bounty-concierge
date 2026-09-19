# GrantFox source baseline — CarbonMint/CarbonMint-Backend #117

Operation: `GFOX3-20260919-carbonmint-carbonmint-backend-117-R-LANTERN`  
Worker: ZZ-Sol-17-Lantern · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `d71a9852fd349a59b8746b9bd80c88867ca2d0da`

## Provider / repository state

- Issue: https://github.com/CarbonMint/CarbonMint-Backend/issues/117
- GrantFox: https://contribute.grantfox.xyz/org/CarbonMint/repo/CarbonMint-Backend/issue/117
- GitHub issue: OPEN, unassigned, 3 comments.
- GrantFox: Unassigned, Apply enabled, one application per user, three visible existing applications.
- Labels: enhancement, GRANTFOX OSS, MAYBE REWARDED, priority:medium, Third Campaign.
- Repository connector authority for this account: pull=true, push=false.
- Fresh open-PR search for issue #117: no carrier surfaced.
- Reward/assignment/payment are not inferred from labels or comments.

This is a pre-assignment source packet. It does not mutate upstream.

## Architecture correction

The issue wording asks for cursor/snapshot pagination, indexed filters, query-plan
checks, and a large-fixture latency target. Current main is **not database-backed**.
The README explicitly describes an in-memory, boot-seeded store; `src/store/index.js`
uses JavaScript `Map` collections.

That matters: an implementation that invents SQL indexes or reports `EXPLAIN`
plans would not test the repository that exists. The source-compatible contract
should preserve the issue's correctness/performance intent while expressing it
through deterministic in-memory selectors, bounded work, secondary lookup
structures only when justified, and large-fixture complexity/latency evidence.

A future durable adapter can map the same public cursor/filter contract to
database indexes without changing API semantics.

## Current pagination / analytics surface

### Batches

`GET /api/batches` is the only relevant public collection already using the
shared pagination helper.

- route blob: `01d8933571d7115bec93bc9b84345af3faf0b98f`
- controller blob: `cbc628243443e78c10323b5ea509aa91725cf116`
- service blob: `292face5f29c8d974f857903a1310e5fbd814675`
- pagination helper blob: `a3f410dd2018b44ded8c7ef90f3bd1829685b8b7`

Current behavior:
- filters: exact `projectId`, `status`, numeric `vintage`;
- service starts with `Array.from(store.batches.values())`, preserving Map insertion order;
- helper accepts 1-based `page` + `limit`, default 20, hard max 100;
- helper computes an offset and slices the mutable array;
- no explicit sort order, snapshot identity, cursor, or tie-break key.

This is bounded in response size but not stable under inserts. If newer-first
ordering is added later, offset pages can duplicate/skip rows as inserts arrive;
under current insertion-order behavior, response semantics still depend on
mutation history rather than a declared API order.

Batch records already carry useful cursor/filter facts: `id`, `projectId`,
`status`, `vintage`, and `createdAt`. Registry must be joined through the
project record because batches do not store registry.

### Projects

- route blob: `7da88a9305d7c092e054017d358ef7c3d0d7f80e`
- controller blob: `97f05a2a24db7748507f8e37ffbd19f360fa3c03`
- service blob: `a977f3fb6df1525325b56b5b82c9bd49aada00db`

`GET /api/projects` returns every project without pagination or filtering.
`GET /api/projects/top` computes stats by scanning all batches separately for
each project, sorts only by `stats.minted` descending, then slices a caller
limit. Equal minted values have no explicit stable tie-breaker.

Project fields include `registry` and boot-generated `createdAt`; these can
support the issue's registry/date filters, but the current API exposes neither.

### Retirement certificates

- retirement route blob: `38046547c090dd6b3d1c5ab5784c98015dd3d232`
- controller blob: `a8afb6117b8affb187dbabde141972ac04042a9f`
- service blob: `83d1c1fb01fe2783ce463e633d165ed8334af203`

`GET /api/certificates` returns the full filtered collection, with optional
exact `user` and `projectId`. It has no page bound, sort contract, registry
filter, status filter, or date window. Certificate records include stable `id`
and `retiredAt`, making them a natural cursor domain.

Every read also verifies certificate integrity and materializes corrections.
Large unbounded reads therefore scale with both certificate count and integrity
work.

### Registry / supply

- registry route blob: `68b470fd3bca4aa83bfd7ed192770faa4f356e28`
- controller blob: `b0c8b2a326a4ebab6679ead41c4fad1fa7bea6f8`
- service blob: `7553c8a153656f69b971dc41173dbbcefcaf31af`

`GET /api/registry` is one aggregate snapshot, not a list. It scans all batches
and all certificates each request and returns totals plus `generatedAt`.
Pagination is therefore not meaningful for this exact response shape. If issue
#117 intends "supply analytics" rows rather than this aggregate, the assigned
implementation should make that new row surface explicit instead of pretending
the aggregate itself needs a cursor.

### Existing sibling pagination precedent

Merged PR #127 introduced admin audit history. Current audit service blob
`c0fed958d3b9ba59a3527f5c348f8f6e49f7e329` sorts by `occurredAt`
descending and then slices using `offset` + `limit` (max 100). It is useful
precedent for bounded inputs but still has the insert-instability class that
#117 explicitly asks to eliminate.

## Source-compatible implementation contract after assignment

### 1. Stable public order

Declare one deterministic order per row type rather than depending on Map order:

- batches: `createdAt DESC, id DESC`;
- projects: either `createdAt DESC, id DESC` for collection views, or
  `minted DESC, id ASC` for ranked analytics;
- certificates: `retiredAt DESC, id DESC`.

The unique ID tie-break is required whenever timestamps or aggregate values tie.

### 2. Opaque cursor + snapshot fence

A practical in-memory cursor can bind:

- schema/version;
- resource kind;
- canonical filter digest;
- snapshot upper-bound captured at the first page;
- last emitted composite sort key.

For append-oriented `createdAt/id` resources, first-page snapshot can bind the
maximum eligible composite key; later pages reject rows outside that snapshot.
This keeps inserts after page 1 from shifting the already-established result set.

For ranked project analytics, where mint/retire activity can change aggregate
sort values for an existing project, a mere last-key cursor is insufficient.
Either:
- materialize a bounded snapshot of project IDs + ranking keys for the request
  lifetime, or
- bind a monotonically increasing store analytics revision and fail the next
  page with a refresh-required error when that revision changes.

Do not silently return a mixed-version ranking.

Cursors should be opaque and integrity-bound (for example canonical JSON plus
HMAC with an application secret in a production adapter). If the current repo
does not yet have secret management appropriate for signing, keep encoding and
verification behind one helper so tests can use a deterministic local key and a
persistent deployment can supply a real secret.

### 3. Bounded filters

Preserve exact, normalized filters in the cursor digest:

- `projectId`;
- `status` where the row type has status;
- `registry` by joining project metadata;
- `from` / `to` ISO timestamps against `createdAt` or `retiredAt`;
- existing `vintage` for batches.

Reject malformed dates and inverted windows. Clamp page size to a documented
maximum rather than accepting an unbounded caller value.

Changing any filter or sort after a cursor is issued must reject the cursor,
not reinterpret it.

### 4. In-memory indexing / bounded work

Because this repo has no database, the useful "index" question is which scans
can be avoided without corrupting mutation semantics.

A focused first implementation can:
- centralize canonical filtering + deterministic sort + cursor logic;
- precompute project lookup by ID using the existing `Map`;
- avoid N×M project-stat scans by aggregating batch totals in one pass for the
  requested snapshot;
- optionally maintain small secondary Sets/Maps for project/status/registry when
  large-fixture benchmarks prove the scan is the bottleneck.

Any secondary index must be updated atomically with the owning in-memory
mutation and covered by consistency tests. Do not add complex indexes merely to
satisfy database-shaped wording.

The issue's "query-plan check" should be represented here as an explicit selector
plan/complexity assertion (for example one batch scan + O(1) project lookup,
rather than one full batch scan per project) and benchmarked on a deterministic
fixture. If/when a database adapter lands, add real database `EXPLAIN` checks
there.

### 5. Response compatibility

Avoid silently changing existing clients from `page` semantics unless the
maintainer approves a breaking contract. A compatibility-safe rollout can:
- add `cursor` / `nextCursor` and deterministic ordering;
- continue accepting page/limit only for a documented deprecation window, or
  add explicit analytics endpoints that use cursor semantics;
- keep existing response data keys and add pagination metadata additively where
  possible.

The assigned PR should state the compatibility choice explicitly.

## Required hostile tests

1. **Concurrent insert:** page 1, insert a newer batch/certificate, page 2 using
   original cursor => no duplicate, no pre-snapshot row omitted because of the
   insert, new row absent until refresh.
2. **Tie keys:** identical timestamps or equal minted totals => ID tie-break gives
   deterministic complete traversal.
3. **Filter binding:** cursor from registry/status/date filter A is rejected
   under filter B.
4. **Bounds:** `limit=0`, negative, NaN, and huge values normalize or reject per
   documented contract; no response exceeds maximum.
5. **Date edges:** inclusive/exclusive semantics fixed and tested; inverted and
   malformed windows fail early.
6. **Rank mutation:** if project aggregates mutate between pages, snapshot
   materialization remains coherent or revision mismatch fails closed.
7. **Certificate integrity:** cursoring cannot bypass existing tamper verification.
8. **Large fixture:** deterministic thousands-of-rows fixture records wall time
   and selector-plan counts; target must be documented for the test machine
   rather than presented as a universal production SLA.
9. **Regression:** demonstrate current offset traversal can shift under an
   inserted row, then show cursor traversal does not.
10. **Existing contract:** current batch filters and unrelated 97+ test baseline
    remain green; no skipped/deleted tests.

## Provider-ready application angle

The three existing applicant comments are generic handler/validation plans and
do not address the repository's actual in-memory architecture or the
cursor/snapshot correctness problem.

A stronger bounded application is:

> I pinned current main and mapped the actual analytics surfaces. The repository
> is in-memory Map storage, so I would not fabricate SQL indexes/query plans.
> I would add deterministic composite ordering and an opaque filter-bound
> cursor/snapshot contract across the relevant collection analytics, preserve the
> current batch page-size cap, remove N×M project-stat work, and treat mutable
> ranked project analytics with a snapshot/revision fence. Tests would reproduce
> offset instability under concurrent inserts, verify tie-break and filter
> binding, cap hostile limits, preserve certificate integrity checks, and benchmark
> a deterministic large fixture with explicit selector-plan counts. I would keep
> API compatibility explicit and focused on #117.

Provider assignment remains a separate durable fact. No implementation should
be represented as assigned until GrantFox/maintainer evidence says so.

## Fleet disposition

- Current lane: source/application research complete.
- Implementation: assignment-gated.
- Existing issue-closing PR: none surfaced.
- Best next action: provider application with the source-specific plan, then
  implement only after assignment; otherwise release the lane for an
  authenticated application seat.
- Reward: possible/discretionary only; no award or payment asserted.
