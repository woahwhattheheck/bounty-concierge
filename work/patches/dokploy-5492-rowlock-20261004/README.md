# Dokploy #5492: serialize moves of the same member

Prepared against `tokenjunkielabs/dokploy@06523165940dfd61d48fdb7977a39bcafa95f03b`, the head of [Dokploy/dokploy#5492](https://github.com/Dokploy/dokploy/pull/5492) when inspected. Target: `apps/dokploy/server/api/routers/organization.ts`, blob `2cf8009b62e87eafb5233dd45c3e42524575b2d4`.

This is an integration packet for the existing PR, not a new upstream submission, assignment, reward claim, or proof of payment. The target PR has not been updated by publishing this packet.

## Defect and repair

The route reads `currentMemberships` before starting its transaction. Two calls moving the same member from A to different teams B and C can capture the same old A membership. Even if their transactions then run serially, the second deletes a now-absent row but decrements A again, and leaves the first destination membership intact. The final `member.teamId` reflects C while both B and C membership rows survive. If A had another member, its counter becomes too small as well.

The patch locks the organization-scoped member row using `SELECT FOR UPDATE`, reads memberships through the same transaction after that lock, and keeps the existing conditional destination-capacity reservation. The transaction returns the member's user ID for the existing audit event. Only `moveMemberToTeam` changes; no schema, dependency, ownership-transfer, invitation, or other endpoint edits.

PostgreSQL's [row-locking documentation](https://www.postgresql.org/docs/18/explicit-locking.html#LOCKING-ROWS) explains the serialization mechanism. The scope is concurrent calls to this route; this packet does not establish equivalent locking in other membership writers or solve all cross-endpoint races.

## Executed verification and limits

`move.before.ts` is the exact retrieved route excerpt at lines 583-677. `move.after.ts` is that excerpt with the patch applied. `check-move.cjs` transpiles those actual route callbacks and supplies deterministic database/ORM collaborators. Its interleaving completes both pre-transaction membership reads before serializing the transaction bodies. This reproduces stale input even under that stronger transaction ordering. It also asserts that candidate membership reads occur after an organization-scoped member row lock.

Executed on Node v22.16.0 / TypeScript 5.8.3: the old callback produces memberships `[B,C]`, final pointer C, and counters A=0/B=1/C=1 despite another member remaining in A. The candidate produces `[C]`, pointer C, and counters A=1/B=0/C=1. Seven checks pass: concurrent moves, full-destination rollback, foreign-member filtering, foreign-team filtering, unassignment, a move to the same full team, and existing destination quota enforcement. Full output is in `check-results.txt`.

These are in-memory collaborator-contract checks, not a real PostgreSQL concurrency test, Drizzle integration test, tRPC authorization test, full typecheck, or application build. The TypeScript step checks syntax/transpilation, not repository-wide types. No network, server, or real organization data is touched. `git apply --check` passed on the exact excerpt at its source line offset; full-file application must be checked in the canonical checkout.

## Apply to the existing PR checkout

First retain the packet outside the target checkout. Set `PACKET` to its absolute directory and enter the existing PR working tree. Refuse a changed source blob rather than replacing someone else's work:

```sh
set -eu
: "${PACKET:?Set PACKET to the absolute packet directory}"
test "$(git hash-object apps/dokploy/server/api/routers/organization.ts)" = 2cf8009b62e87eafb5233dd45c3e42524575b2d4
git apply --check "$PACKET/move-rowlock.patch"
git apply "$PACKET/move-rowlock.patch"
git diff --check
```

Re-run the retained reproduction with an already-installed TypeScript package:

```sh
NODE_PATH="$(npm root -g)" node "$PACKET/check-move.cjs" "$PACKET/move.before.ts" "$PACKET/move.after.ts"
```

For application-level acceptance, exercise two simultaneous moves of the same member against the actual PostgreSQL/Drizzle route, verify membership rows and all affected team counters after both calls, and retain the existing full-destination rollback check. Preserve the original upstream PR and branch; no duplicate claim or PR is needed.
