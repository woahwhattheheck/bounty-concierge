# TradeFlow-API #43 — source-specific application packet

Use only after a fresh GrantFox/GitHub census confirms #43 is still unassigned and has no active implementation carrier.

## Draft

I'd like to take BETAIL-BOYS/TradeFlow-API #43. I reviewed current `main@666b47088ae51cf0b0e657a8d56872b40fd327c7` and the production lifecycle before proposing the patch.

The missing SIGTERM/SIGINT handler is real in `src/main.ts`, but this Nest app has more shutdown ownership than a bare `server.close(); process.exit(0)` change would cover. Prisma and the app's publisher/subscriber Redis clients already clean up through Nest `onModuleDestroy`, while the express-rate-limit Redis client from `config/redis.js` sits outside Nest ownership. The background `IndexerJob` also discards its cron task handle and the bootstrap drops the job instance, so there is currently no way to stop new indexer work during shutdown.

My implementation would retain the app/HTTP server, IndexerJob, and optional standalone rate-limit Redis owner, then route SIGTERM and SIGINT through one idempotent shutdown state machine. The first signal would stop new background scheduling, log `Gracefully shutting down TradeFlow API...` once, stop HTTP admission and drain active requests, then close the Nest app so Prisma/Redis/module hooks execute and finally close the standalone Redis client. A bounded timeout/failure path would prevent a wedged connection from hanging deployment forever; clean exit 0 would happen only after successful cleanup, while cleanup failures would not be reported as graceful success.

I would add focused Jest/integration coverage using a real listening server where useful: SIGTERM/SIGINT parity, in-flight request drain, rejection of new requests once shutdown starts, Nest destruction hooks, standalone Redis cleanup, IndexerJob stop, rapid double-signal idempotence, cleanup failure, and forced-timeout behavior. The repo already has Jest/Supertest and colocated Nest integration tests. I would not depend on the current `test:e2e` script's missing `test/jest-e2e.json` unless maintainers want that adjacent test-command repair included.

If assigned, I would re-census current main and issue-linked PRs immediately before changing source, keep the patch scoped to lifecycle ownership, and avoid copying the legacy `scripts/server.js` pattern that exits immediately after WebSocket close.
