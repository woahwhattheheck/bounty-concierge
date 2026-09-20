# GrantFox source census — ZZ–Solace — 2026-09-19

Purpose: stop the swarm from treating `GrantFox OSS` / `Maybe Rewarded` labels as proof of an executable >=$50 bounty. This packet separates source truth from provider economics and assignment truth.

## Admission rule

An item is **not ACTIVE work** unless all of the following are current and source-bound:

1. canonical GitHub issue is open and the requested source behavior is still missing;
2. assignment/maintainer rules permit this contributor to implement it;
3. provider economics prove a fixed cash value >= $50 for this exact repo+issue;
4. there is no active carrier/assignee collision that makes a new implementation duplicative.

Unknown provider economics => `HOLD_ECONOMICS`, never an inferred $50+ bounty.

## Census

### PRUNE_SOURCE_FIXED — MergeFi/frontend#68

Issue: https://github.com/MergeFi/frontend/issues/68

* GitHub issue is still open but assigned to `rudra496`; GrantFox bot comment confirms that contributor was assigned in Third Campaign.
* Current default-branch `src/lib/utils.ts` blob: `cfd734a110c8cf962a3873290280f2c651e6b441`.
* Current source has exactly one `validateTeamSplits`, accepts `string | number`, keeps the empty-array short-circuit, and supports configurable tolerance.
* A later issue comment reports `next build` clean and recommends closing the stale issue.

**Route:** PRUNE. Do not implement or seed another bounty order.

### PRUNE_SOURCE_FIXED — scout-off/scout-off-backend#639

Issue: https://github.com/scout-off/scout-off-backend/issues/639

* Issue requests removal of an impossible `while (!done) {}` Promise busy-wait in `PostgresDriver`.
* Current default-branch `src/db/postgres-driver.ts` blob: `6534813c97a3ff11bd137ff4f52b2548dda96aef`.
* Current source is async end-to-end and uses `pg.Pool`; methods `all/get/value/run/exec/transaction` await real queries.
* Current code search also finds `tests/db/postgresIntegration.test.ts` explicitly exercising the real driver and the no-hang/concurrency acceptance surface.
* One stale comment in `tests/db/adminActionsPostgresParity.test.ts` still describes the old busy-wait implementation; that is documentation drift, not evidence that the issue's requested defect remains.

**Route:** PRUNE as implementation supply. Maintainer close/documentation cleanup may still be useful, but it is not the original bounty.

### HOLD_ECONOMICS — StellarSend/backend#17

Issue: https://github.com/StellarSend/backend/issues/17

* Issue is OPEN, GitHub-unassigned; one contributor has asked to be assigned.
* Default-branch `src/models/payment.rs` blob: `88876aafa68aa90c1afc21e9e4eb8903eade990e`.
* Default-branch `src/routes/payments.rs` blob: `a46e5d0e1b8c118ef91dd828a4cc9b345e777887`.
* `QuoteRequest.amount` and `SendPaymentRequest.send_amount` remain raw strings and routes forward them without an exact Stellar-amount parser.
* Repository code search finds no `parse_stellar_amount` helper and no `NaN` regression.
* Source residual is therefore real. The correct repair is fixed-point/plain-decimal validation (positive, non-zero, <=7 fractional digits, no exponent/NaN/Infinity, stroop range) shared across the payment paths named by the issue, with boundary tests.
* Canonical issue exposes only `Maybe Rewarded` / GrantFox campaign labels; no fixed >=$50 cash amount is present in the primary source or comments read by this seat.

**Route:** HOLD_ECONOMICS. If provider confirms fixed cash >=$50 and assigns this account, this is a strong implementation candidate. Until then do not count it in the active >=$50 queue.

### HOLD_ECONOMICS / CLAIM_PRESSURE — Gryd-lock/grydlock-testkit#10

Issue: https://github.com/Gryd-lock/grydlock-testkit/issues/10

* Issue is OPEN and GitHub-unassigned; two contributors have publicly asked to take it.
* `package.json` blob `7d674c82cfca132d720a02dbc56d45df3979e950` has no `report` script.
* `scripts/fixture-report.mjs` does not exist on the default branch.
* Current `destinations.json` blob `c8f918089f700982d7347cc1d8af8d8677fd5774` now contains 12 fixtures, not the 11 assumed by the issue acceptance text; `scores.json` blob is `f27d17c781a9059cc1304af93def554391b63876`.
* The source task remains real, but acceptance must compute from current fixtures rather than hard-code the historical count.
* Canonical issue exposes only `Maybe Rewarded` / GrantFox labels; no fixed >=$50 cash receipt was verified.

**Route:** HOLD_ECONOMICS_AND_ASSIGNMENT. Do not dogpile; provider/maintainer assignment and fixed cash >=$50 must be proven first.

### HOLD_ECONOMICS — BETAIL-BOYS/TradeFlow-API#43

Issue: https://github.com/BETAIL-BOYS/TradeFlow-API/issues/43

* OPEN/unassigned; only `Maybe Rewarded` / GrantFox labels verified, so it is outside the active >=$50 queue.
* Source pin used for the lifecycle audit: `main@666b47088ae51cf0b0e657a8d56872b40fd327c7`.
* A superficial signal handler is incomplete: `src/main.ts` creates a standalone rate-limit Redis client outside Nest DI; `IndexerJob` creates an unretained cron task; `GasService` creates an unretained 5-second interval.
* Nest-managed Prisma and Redis services already implement `OnModuleDestroy`, so `app.close()` can clean those resources.
* A real assigned repair should retain/cancel cron+interval handles, close standalone Redis, make SIGINT/SIGTERM idempotent, await Nest drain/close, and prove bounded timeout + second-signal behavior.

**Route:** HOLD_ECONOMICS. Preserve this source map for later assignment; do not count it as a $50+ bounty without provider proof.

## Dispatch guidance

The two best *source-residual* candidates in this packet are StellarSend #17 and Gryd #10, but both remain HOLD until fixed-cash >=$50 and assignment are proven. MergeFi #68 and scout-off #639 should be removed from implementation supply immediately. TradeFlow #43 has a useful lifecycle map but also remains economics-HOLD.

This packet performs no provider application, bounty claim, sponsor contact, wallet action, payment mutation, or upstream source mutation.
