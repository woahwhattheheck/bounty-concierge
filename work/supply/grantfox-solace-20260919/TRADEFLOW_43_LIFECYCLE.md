# TradeFlow #43 lifecycle repair map

Source: `BETAIL-BOYS/TradeFlow-API#43` at `666b47088ae51cf0b0e657a8d56872b40fd327c7`.

This is **not an active bounty order** because fixed cash >= $50 was not verified. It is retained as assignment-ready source evidence.

## Resource ownership

| resource | source | current shutdown behavior | required repair |
|---|---|---|---|
| Nest HTTP/application | `src/main.ts` | no signal handler | one idempotent shutdown promise; await app drain/close |
| Prisma | `src/prisma/prisma.service.ts` | `OnModuleDestroy -> $disconnect()` | let `app.close()` own it |
| Nest Redis publisher/subscriber | `src/common/redis/redis.service.ts` | `OnModuleDestroy -> disconnect()` | let `app.close()` own it |
| rate-limit Redis | `config/redis.js` imported by `src/main.ts` | outside Nest DI, no close path | explicitly close/quit after admission stops |
| indexer cron | `src/jobs/indexer.ts` | `cron.schedule()` return value discarded | retain task and stop/destroy it |
| gas polling | `src/gas/gas.service.ts` | `setInterval()` handle discarded | retain timer + clear in lifecycle destroy |
| WebSocket gateway | `src/trade/trade.gateway.ts` | Nest-managed | close through application lifecycle; verify sockets do not extend shutdown indefinitely |

## Required signal semantics

* SIGINT and SIGTERM enter the **same** shutdown promise.
* First signal stops admission and starts cleanup.
* A second signal cannot start a second cleanup chain or double-close resources.
* Normal exit occurs only after cleanup resolves.
* A bounded fallback timer is allowed so a wedged connection cannot hang deployment forever; it must be test-visible and cleared after successful shutdown.
* Shutdown failures must yield a non-zero exit rather than falsely reporting a graceful success.

## Regression surface

1. real listening server + in-flight request: no new admission after shutdown starts; active request can finish;
2. SIGINT then SIGTERM: cleanup methods called once;
3. hanging connection: bounded fallback activates;
4. successful drain: fallback cancelled and exit 0 only after cleanup;
5. cron and gas polling do not fire after shutdown;
6. standalone rate-limit Redis closes even though it is outside Nest DI.

Do not reduce this issue to `process.on(...); server.close(); process.exit(0)`; that leaves source-owned background resources alive and can exit before Nest cleanup finishes.
