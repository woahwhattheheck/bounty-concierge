# TradeFlow-API #43 — graceful shutdown source baseline

Status: source-audited; GrantFox/maintainer assignment required before upstream implementation.

## Target

- Upstream: `BETAIL-BOYS/TradeFlow-API`
- Issue: #43 — **chore: Implement a graceful shutdown sequence for the Node server**
- Upstream source generation: `main@666b47088ae51cf0b0e657a8d56872b40fd327c7`
- GitHub issue state at census: OPEN, unassigned, 2 comments
- Open PR census for issue number 43: 0
- GrantFox live page at census: Apply enabled, 1 application per user, Direct GitHub comment, Assigned to **Unassigned**
- Exact same-day Slack repo/#43 search before TAKE: 0 current claim results
- Upstream permission from connected account: pull=true, push=false
- Reward truth: Maybe Rewarded / GrantFox OSS / Official Campaign. No cash amount or award is asserted here.

This is a pre-assignment source/application packet. It performs no upstream source mutation, provider application, assignment, deployment, credential, wallet, payment, or reward action.

## Current lifecycle topology

The issue correctly identifies the missing signal-handling path in the production Nest entrypoint, but a safe implementation is broader than a bare `server.close(); process.exit(0)`.

### Nest application + HTTP server

`src/main.ts` (blob `87169f559892fb7b8227b14664e85792085983f4`) creates the Nest app, installs middleware/rate limiters, calls `await app.listen(port)`, then instantiates `new IndexerJob()`. It currently registers no SIGTERM/SIGINT handler.

The production start path in `package.json` (blob `9ead7ac4633b2fa0c08f21adfeb483fe16e25f68`) is `node dist/main`, so this is the primary lifecycle surface for issue #43.

A graceful path must retain a handle to the Nest app and HTTP server and stop accepting new HTTP connections before final process exit.

### Nest-owned resources need `app.close()`, not only raw HTTP close

Two important resources already participate in Nest destruction:

- `src/prisma/prisma.service.ts` (blob `bb6565f3785cfbfae4c4da9be9a4cad166f1e981`) implements `onModuleDestroy()` and disconnects Prisma.
- `src/common/redis/redis.service.ts` (blob `ed6c0709ac9634cb216627277a642f5b48d3d1b3`) owns publisher/subscriber ioredis clients and disconnects them in `onModuleDestroy()`.

`src/app.module.ts` (blob `22d79d76e63ea72629f2381c3255790f64f70a39`) imports both `RedisModule` and `PrismaModule`.

Therefore a shutdown sequence that calls only the underlying HTTP server's `close()` and then `process.exit(0)` can bypass module-destruction cleanup. The implementation should deliberately close the Nest application after admission/drain so registered lifecycle hooks execute.

### A second Redis client is outside Nest ownership

`src/main.ts` conditionally requires `config/redis.js` for `express-rate-limit`. The module (blob `2aeccd07a5fe209ed1f45ac3ea2613a89bc05403`) constructs a standalone ioredis client with an unbounded reconnect strategy and exports it directly.

That client is not owned by `RedisService`, so `app.close()` alone will not close it. The shutdown owner must explicitly terminate this rate-limit Redis client when present, ideally after the HTTP drain has stopped new rate-limit work.

The surrounding `try/catch` only catches module construction exceptions; an unreachable Redis normally reports asynchronously through the client's `error` event. This packet does not broaden #43 into a Redis availability rewrite, but shutdown must not mistake that module-scope client for a Nest-owned resource.

### IndexerJob currently has no shutdown contract

`src/jobs/indexer.ts` (blob `d095cefbb4a873a5507efdbaffaff7f928a0fc27`) calls `cron.schedule()` in its constructor but discards the returned task handle. Its simulated sync work also starts an untracked two-second `setTimeout`.

Because `src/main.ts` creates the job with `new IndexerJob()` and drops the instance, there is currently no way for a signal handler to:

- stop future cron admissions,
- distinguish idle from in-flight sync work,
- await or cancel current work,
- prove the job cannot restart work during HTTP shutdown.

The assigned implementation should retain the job instance and give it an idempotent shutdown/stop contract. At minimum, retain and stop/destroy the scheduled task. If the simulated work becomes real asynchronous work, define whether shutdown drains or aborts it and test that behavior.

### WebSocket and subscription resources are Nest-managed

`src/trade/trade.gateway.ts` (blob `d4e2f6431159f490de812ef636ab8f53b9c45dc6`) creates a Nest WebSocketGateway and subscribes through the Nest-owned `RedisService`. Closing the Nest app is therefore the correct lifecycle seam for the gateway/subscription side rather than inventing a second unmanaged socket shutdown path.

There is also a legacy/standalone `scripts/server.js` (blob `9b8883a97b2251c6e369c9923297d62d2af00d51`) with a SIGTERM handler that calls `wss.close()` and immediately `process.exit(0)`. It is not the `start:prod` target and does not satisfy #43's Nest HTTP drain. Do not copy its immediate-exit pattern into `src/main.ts`.

## Recommended shutdown contract

A strong assigned implementation should make shutdown one explicit idempotent state machine.

1. **Retain ownership**
   - retain `app`, the underlying HTTP server, the `IndexerJob`, and the optional standalone rate-limit Redis client;
   - keep ownership local to bootstrap/lifecycle code rather than scattering signal listeners across services.

2. **Single signal path**
   - register SIGTERM and SIGINT;
   - first signal atomically transitions RUNNING -> SHUTTING_DOWN;
   - later signals do not start a second concurrent close sequence. A deliberate escalation policy is acceptable, but it must be explicit and tested.

3. **Stop admission before teardown**
   - stop future IndexerJob scheduling;
   - call the HTTP server close path so new connections are refused while active requests can finish;
   - emit the issue-required `Gracefully shutting down TradeFlow API...` log once.

4. **Drain and Nest teardown**
   - wait for HTTP close completion;
   - close the Nest application so Prisma, Nest Redis, WebSocket gateway, and module hooks receive teardown;
   - close the standalone rate-limit Redis client separately.

5. **Bounded failure policy**
   - successful complete teardown exits 0 only after cleanup resolves;
   - teardown failures should exit non-zero and preserve diagnostic context;
   - add a bounded timeout/escalation so a wedged request/socket cannot leave a deployment hung indefinitely. This timeout is a safety supplement, not permission to exit 0 before cleanup.

6. **No duplicate exit races**
   - timeout, server-close callback, Nest close, Redis cleanup, and a second signal must not race into multiple `process.exit()` calls or double-dispose resources.

## Test seam

The repository already has Jest, Supertest, and Nest testing dependencies. Existing colocated integration tests such as `src/pools/pools.controller.int.spec.ts` (blob `2418a40b6140778b7fd85dc140301c09a2924502`) create an app and close it in `afterAll`.

`package.json` advertises `test:e2e` with `test/jest-e2e.json`, but that path is absent on pinned main. Do not make #43's validation depend on a nonexistent config; use a focused Jest spec or repair the test command only if maintainers want that adjacent cleanup.

Required hostile/behavioral tests:

- SIGTERM and SIGINT both enter the same shutdown path;
- first signal logs exactly once;
- a real listening HTTP server stops accepting new requests;
- an in-flight request is allowed to finish before successful shutdown;
- Nest `onModuleDestroy` resources are invoked;
- standalone rate-limit Redis cleanup is invoked exactly once;
- IndexerJob stops future cron admissions;
- two rapid signals cannot double-close/double-exit;
- clean drain exits 0 only after all cleanup completes;
- HTTP/Nest/Redis cleanup rejection yields a non-zero failure path;
- forced-timeout path terminates once and is not reported as graceful success.

## Assignment fence

Immediately before implementation, re-census GrantFox assignment, GitHub assignee/comments, current `main`, and issue-linked PRs. Upstream is pull-only for the connected account. Do not mutate upstream source from this packet alone.
