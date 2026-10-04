# Dokploy #5492: real PostgreSQL acceptance

KESTREL-47 / GPT-6 Astra Pro / ChatGPT cloud harness. Work order `WO-F89E-DOKPLOY5492-REAL-PG`. This complements the original Kestrel packet; it does not replace its source ownership or create another upstream PR or bounty claim.

## Inputs and execution

The source is [tokenjunkielabs/dokploy@06523165](https://github.com/tokenjunkielabs/dokploy/commit/06523165940dfd61d48fdb7977a39bcafa95f03b), belonging to [Dokploy/dokploy#5492](https://github.com/Dokploy/dokploy/pull/5492). The unchanged repair is [the retained packet at f5d7b088](https://github.com/woahwhattheheck/bounty-concierge/tree/f5d7b088a038311f912b45e880a5a427646096c0/work/patches/dokploy-5492-rowlock-20261004). Both are checked out by exact commit. Full router, table declarations, before/after excerpts and patch are guarded by Git blob hashes.

The branch-scoped workflow `.github/workflows/dokploy5492-real-pg-k47.yml` uses one disposable PostgreSQL 17 service, Node 24.4.0 and the exact declared Drizzle 0.45.2 / postgres-js 3.4.4 / nanoid 3.3.18 / tRPC 11.10.0 dependencies. TypeScript 5.8.3 only transpiles the retained callbacks and table declarations; it does not claim a repository typecheck. There is no application install, build matrix, new VM, deployment, owner-PC execution, production database or live credential.

`real-pg.cjs` executes the actual before/after mutation callbacks through real Drizzle and PostgreSQL transactions at READ COMMITTED. A third transaction locks the original membership row; both route calls begin and their actual PostgreSQL lock waits are observed before that blocker releases. No database result, transaction or ORM method is mocked.

The four bounded checks are the old race, the patched race, full-destination rollback and two members competing for one free slot. Raw row snapshots, stored-versus-actual counts, wait queries, server version, source hashes, dependency lock and applied patch are retained as workflow artifacts. The script refuses non-loopback database addresses and any database name other than `dokploy5492_acceptance`.

## Scope and limitations

The four relevant table declarations are extracted unchanged from the pinned production `account.ts`. Their column types, defaults and foreign/unique/primary-key constraints generate the proof schema. The unrelated parent user table is projected to its referenced primary key; nonunique indices are not created. Audit delivery is a local collector. This is route-callback/database acceptance, not authentication, tRPC transport, complete production migrations, cross-endpoint concurrency, an application build or a throughput benchmark.

The workflow is source-pinned and single-job, so another agent can reuse the evidence instead of rechecking the same patch. A source change requires a separately justified rebase of the proof, not silently accepting a mismatched blob. The original upstream PR remains the only implementation/submission carrier. No maintainer acceptance, award or payment is inferred from a passing internal run.

## Status

Completed: all four checks passed on PostgreSQL 17.11 in run `37189908947`. See [RESULTS.md](RESULTS.md) and the original [report.json](report.json). The executed runner and workflow remain unchanged; this update only retains the observed evidence.
