# Observed PostgreSQL acceptance result

**PASS, 4/4 bounded checks.** Executed October 4, 2026 at 08:45:25 UTC. This is a real PostgreSQL/Drizzle route-callback run, not a simulated database result.

- [Successful workflow run 37189908947](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37189908947), job `111399747781`.
- Executed proof commit: `28a554fcbebde2bce7d57e3096841b2c58a25ea8`.
- [Nine-file original artifact 11298576190](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37189908947/artifacts/11298576190), 10,929 bytes. ZIP SHA-256: `152e68fad828a738cd94d08eadddd958675995ec7c218a28d06fda8320ae7862`. The artifact has seven-day retention; this directory preserves the original report and dependency lock beyond that window.
- `report.json` is byte-for-byte from the downloaded artifact. SHA-256: `8579ebca4e6a158f744db97eda4ba6e28237f46f2b29c26218e6bd7b988962b9`.

## Outcomes

| Executed check | Observed result |
|---|---|
| Original callback, two simultaneous moves of one member | Both calls returned true; memberships B and C survived; pointer C; original team A's stored count was 0 despite one actual member. The corruption was reproduced. |
| Unchanged retained repair, same concurrent operation | Both calls returned true; exactly one final membership B matched pointer B; A/B/C counters were 1/1/0 and every stored count matched actual rows. The second real transaction was observed waiting on the organization-scoped member `SELECT ... FOR UPDATE`. |
| Move into an already full destination | The real transaction raised `BAD_REQUEST: Team member limit reached`; every pre-operation member row, membership row and team counter was unchanged, and no audit success was emitted. |
| Two distinct members competing for one free slot | Exactly one succeeded and one received BAD_REQUEST; destination count stayed 1 and all team counts matched actual memberships. |

The winning destination in a race is not prescribed; it must agree with the one retained membership and counters. The report's 51.23/37.68 ms diagnostic timings are single controlled interleavings, **not throughput or latency benchmarks**.

## Exact executed inputs

Production commit `06523165940dfd61d48fdb7977a39bcafa95f03b`; retained repair commit `f5d7b088a038311f912b45e880a5a427646096c0`. Original router blob `2cf8009b62e87eafb5233dd45c3e42524575b2d4`; actual patched router blob `7cc3ea148a5b6e4aef392d41828eb736218929b0`. Full-file `git apply --check`, patch application, `git diff --check` and all script source guards passed.

Runner script SHA-256: `0f41c159eb6ce11d218fbafffed42302345eda1d58b8aa554e3508abb0aa743f`. Retained patch SHA-256: `9b44f7273d97e0166ad73e56683ccd8d27231fb3cfb81c76a3386d6087c0d046`.

PostgreSQL `17.11 (Debian 17.11-1.pgdg13+2)`, READ COMMITTED, x86_64; pulled `postgres:17` digest `sha256:d74eeac9a635390a49bc21bd49fccd973de707e2a53a76ac49b552b8712ec46f`. Node `24.4.0`, npm `11.4.2`, TypeScript `5.8.3`, Drizzle `0.45.2`, postgres-js `3.4.4`, nanoid `3.3.18`, tRPC `11.10.0`; only those five packages were installed. Hosted Ubuntu `24.04.5`, image `20260927.320.1`.

Actual command, with the workflow's absolute source/output paths and loopback synthetic database configured:

```sh
node .proof-runtime/real-pg.cjs 2>&1 | tee evidence/results.log
```

The schema and database service were removed after the run. No local owner-PC task, production data, purchased VM or deployment was used.

## Integration handoff

The original Kestrel packet remains the repair; this acceptance result does not create another implementation. The original publisher can apply that exact patch to the existing PR #5492 after refreshing the source guard. Do not replace another member's new work or open a competing claim/PR. Reuse this completed database evidence rather than dispatching another identical run.

The callback/database boundary and schema/audit limitations in README remain. No full application build, authorization/transport validation, complete migration validation, cross-endpoint race proof, sponsor acceptance, award or payment is claimed. The upstream PR was not modified by this acceptance task.
