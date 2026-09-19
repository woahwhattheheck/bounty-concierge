# GrantFox baseline — Bitcoindefi/Open-Stellar #114

Operation: `GFOX3-20260919-OPEN-STELLAR-114/R-dependency-map`  
Worker: ZZ-Solstice · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `2edade4dda9f4f417845ae0ba74cb186d7dd6474`

## Live provider / issue state

- Issue: https://github.com/Bitcoindefi/Open-Stellar/issues/114
- GrantFox: https://contribute.grantfox.xyz/org/Bitcoindefi/repo/Open-Stellar/issue/114
- GitHub state: **OPEN**, no assignee, 3 comments.
- GrantFox public page: **Assigned to → Unassigned**, Apply control visible.
- Labels: `area: agents`, `milestone: v0.4`, `GrantFox OSS`, `Maybe Rewarded`, `Official Campaign`, `Third Campaign`.
- Maintainer `leocagli` explicitly unassigned #114 for inactivity on 2026-08-16 and wrote that anyone else is welcome to claim it.
- No fixed reward, award, acceptance, or payment is asserted by this packet.
- Upstream connector permission is read-only (`pull=true`, `push=false`).

This is a high-impact money-state/concurrency issue, but implementation is dependency-fenced. The issue explicitly requires #114 to consume the state/transitions defined by #113 and forbids inventing a second state machine.

## Dependency truth: #113 is active and assigned

Issue #113, “Task offer API — agents post work offers with locked XLM reward,” is OPEN and currently assigned to `@Unclebaffa` by both GrantFox bot and maintainer comments dated 2026-08-19.

Its required invariants include:

- lock the reward before an offer becomes visible;
- insufficient balance leaves no partial offer;
- one canonical state-transition definition shared with #114;
- expiry refunds an unclaimed offer;
- duplicate idempotency-key POSTs create one offer;
- amounts are integers in the smallest unit.

At pinned upstream main `2edade4d...`, the expected task-offer implementation paths are still absent:

- `lib/task-market/offers.ts`
- `lib/task-offers/store.ts`
- `lib/task-offers/escrow.ts`
- `app/api/task-offers/route.ts`
- `app/api/task-offers/[id]/route.ts`

So #114 cannot yet bind to a landed canonical #113 store/state-machine seam.

## Why old open PR #440 is not the dependency to build on

Open PR #440, `Add task offer API`, predates the current #113 assignment.

Observed PR:

- head: `mircats98gpt/Open-Stellar@09a6e7374bec86fd17f4d6dce58bb62d2841939f`
- changed files: 4
- source `lib/task-market/offers.ts` blob: `bed1531e242f900d56623d8091a467f9646d1259`
- list/create route blob: `2b4acc11dafdb58e89d664db69fe88eb9ee62034`
- detail/delete route blob: `6063f79936c734ca0a8feb7dfbbef4d19d60c5b2`
- test blob: `6fa1c1bfdc4f817381558d50f0a07cc0b6202339`

The PR itself says escrow/refund are represented as API-layer tracking IDs “so the REST flow can be wired to the Soroban client later.”

Source confirms the mismatch:

- state is a process-local `Map` on `globalThis`;
- `escrowTx` / `refundTx` are generated strings from `crypto.randomUUID()`, not proof of locked/released funds;
- rewards remain decimal strings and are validated with `Number(...)`, while #113 explicitly requires integer smallest-unit arithmetic;
- there is no idempotency-key path;
- expiry fabricates a refund tracking ID rather than exercising the required real refund invariant.

Therefore #440 can be mined for API shape ideas but must not be treated as #113’s authoritative financial substrate for #114.

## #114 implementation contract after #113 lands

The assigned implementer should first refresh #113’s landed commit and bind to its exact store/state transition/escrow APIs. Then implement one coherent lifecycle extension:

1. **Atomic claim**
   - transition only `open -> claimed`;
   - concurrent claims must produce exactly one winner;
   - loser gets a deterministic conflict response containing current state.

2. **Authorized delivery**
   - only the recorded claimant can transition `claimed -> delivered`;
   - result storage and transition audit must be one coherent operation.

3. **Idempotent accept / release**
   - only poster can accept;
   - release escrow once;
   - retries/double-submit cannot pay twice;
   - transition + payout receipt must be durable enough to distinguish “payment committed, response lost” from “not paid”.

4. **Dispute freeze**
   - only poster can open dispute;
   - disputed reward remains frozen: neither worker release nor poster refund until resolution;
   - #114 opens the dispute only; arbitration UI/resolution stays out of scope.

5. **Expiry and timeout**
   - expired unclaimed offers cannot be claimed;
   - delivered-but-unaccepted offers auto-release after 10 minutes;
   - timeout worker and explicit accept must share the same idempotent release primitive so they cannot race into duplicate payout.

6. **Conflict contract**
   - invalid transitions return HTTP 409 with current state rather than generic 500;
   - every valid state change records actor and timestamp.

## Hostile / concurrency tests required

Beyond happy-path endpoint tests, the assigned implementation should prove:

- two concurrent claim attempts -> exactly one winner and one 409 loser;
- claimant-only delivery rejects a third party without state drift;
- two concurrent/retried accept calls -> one release receipt / one payout;
- timeout auto-release racing manual accept -> one payout;
- dispute racing accept -> exactly one terminal money-moving decision according to the canonical transition primitive;
- expired-open claim -> rejected, no ownership mutation;
- invalid transition -> 409 + canonical current state;
- transition audit records actor/time exactly once per committed transition;
- server restart/retry semantics do not convert response loss into double release.

## Companion issue #156

#156 (task-board GET/error-state integration) is also publicly Unassigned after an inactivity unassignment. It has a new applicant comment from 2026-09-17.

Current main still lacks the task-offer endpoint. #156 explicitly says to consume #113’s store if #113 lands. Treat it as a companion UI/API integration slice, not a reason to create a competing store while #113 is assigned.

## Assignment-ready application note

> Applying for #114 after checking current `main@2edade4dda9f4f417845ae0ba74cb186d7dd6474`.
>
> I traced the dependency rather than starting a second state machine: #113 is currently assigned to @Unclebaffa, and the task-offer store/API paths are not yet on main. The older open #440 is not safe as the money substrate: it uses a process-local Map, random escrow/refund tracking IDs, decimal Number parsing, and no idempotency key, while #113 explicitly requires real locked-fund invariants, smallest-unit integer amounts, and canonical transitions shared with #114.
>
> For #114 I would wait for/rebase onto the assigned #113 carrier, then implement atomic first-winner claim, claimant-only delivery, poster-only accept/dispute, one idempotent release primitive shared by manual accept and the 10-minute timeout, frozen dispute funds, 409/current-state conflicts, and actor/timestamp audit. The key hostile tests are concurrent claim, double/retried accept, timeout-vs-accept, and dispute-vs-accept so no interleaving can pay twice or give two agents ownership.
>
> I will keep arbitration UI/resolution and a second offer-state machine out of scope.

## Authority fence

This packet performs no upstream code change, provider assignment, application, wallet/funds operation, payout, or reward claim. Before implementation, refresh #113 assignment/carrier/landed SHA and #114 provider state.
