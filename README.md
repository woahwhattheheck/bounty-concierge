# Commitlabs #1942 timer lifecycle candidate

This is an isolated public validation branch, not a change to Bounty Concierge main and not an upstream submission.

Target: `Commitlabs-Org/Commitlabs-Frontend#1942`, upstream master `0d847032ef5862806cbb678f0bca93b6be44fb17`.

The upstream file already has `clearCleanupInterval()`, but its module-local state creates another timer and store whenever the module is evaluated again. The candidate keeps the store and timer together on a namespaced global symbol, keeps the existing clear function usable from old module copies, and unrefs Node maintenance timers. Browser numeric timer handles remain supported. No idempotency record, TTL, or service method is changed.

Candidate product blob: `4f116f46a536ce09afc640b094c1f19684dddce1`.
Candidate six-case regression blob: `85d4012714e93b7a3dc7fdbdc8d7f6ae33e08c9d`.

A real Node 22.16.0 local check evaluated the actual transpiled module six times: baseline created six referenced intervals and retained five after clearing through the oldest module; candidate created one unreferenced interval and retained zero after clearing. Completed record replay was shared only by the candidate. An ordinary candidate import then allowed the Node process to exit without a manual clear.

The workflow runs the six new Vitest cases on baseline, then these cases plus the existing idempotency unit file on the candidate. It uses Node 20 and isolated Vitest/coverage-v8 2.1.9 dependencies, which satisfy the repository's declared Vitest range. It does not claim the full application install, original jsdom setup, project typecheck, full build, deployment, reward or payment. Raw coverage and the exact temporary runner lock are retained in the artifact.

Only `idempotency.ts` and `idempotency.cleanup.test.ts` are intended product changes, at their normal `src/lib/backend/` and `src/lib/backend/__tests__/` paths. Do not merge this validation branch into Bounty Concierge main. An ordinary upstream-compatible fork and the current campaign decision remain publication prerequisites.
