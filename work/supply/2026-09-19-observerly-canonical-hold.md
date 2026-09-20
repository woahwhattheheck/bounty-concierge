# Observerly fixed-cash canonical-source verification — 2026-09-19

Owner: **ZZ-Sol-Javelin-884 / GPT-5.6 Sol**

## Decision

**HOLD_CANONICAL_SOURCE_UNAVAILABLE** for both currently advertised >=$50 Observerly rows:

| Provider row | Advertised fixed cash | Provider age | Current decision |
|---|---:|---:|---|
| OBS-17 / SIRIU-17 — `feat(app): add UserPrompt on initial app start to TelescopeListboxSelect component` | $100 | ~32 months | HOLD |
| OBS-15 — `ci(app): deploy apps/web on tag @latest to Vercel` | $60 | ~33 months | HOLD |

These clear the owner's nominal $50 floor **only at the marketplace layer**. They do not yet authorize implementation work.

## What was verified

1. The current Observerly Algora board still renders the rows as open fixed-cash bounties: $100 for OBS-17/SIRIU-17 and $60 for OBS-15.
2. The board identifies both as imports **from SyncLinear.com**, not canonical public GitHub issues.
3. Exact-title public source discovery for the task names did not resolve a canonical GitHub issue or public application repository.
4. Observerly's public GitHub organization is live and exposes open-source astronomy libraries, but the current verification pass did not establish a public repository containing the referenced `apps/web` application generation or `TelescopeListboxSelect` component.
5. No same-day Slack TAKE/PROGRESS/DONE/HOLD collision existed for OBS-17 or OBS-15 immediately before this verification.

## Why this is a HOLD, not a TAKE

The swarm's owner rule requires more than a marketplace card. A builder needs a reproducible canonical task/source generation so it can prove:

- the issue is still genuinely open;
- the requested source gap still exists;
- no maintainer stop, reservation, assignee, or implementation carrier exists;
- the code to change is accessible through an authorized publication route;
- acceptance can be tested against current source rather than a 32–33-month-old imported description.

None of those source-generation facts can be established from the current public card alone. The old imported rows therefore must not consume an implementation seat.

## Re-activation gate

Promote either row only if a fresh free-source check produces **all** of:

1. canonical task URL owned by Observerly (or an authoritative Linear/public tracker record);
2. current source repository + exact ref for the affected application;
3. task still open and still funded at the advertised fixed amount;
4. no assignee/reservation/maintainer hold/current implementation carrier;
5. usable contribution/claim route;
6. fresh Slack collision census.

If the application/task is private, the provider must expose an authorized source/contribution route before engineering starts.

## Sources

- https://algora.io/observerly/bounties
- https://github.com/observerly

This packet does not claim the bounty is cancelled, paid, or fraudulent. It records only that the **canonical implementation generation is not presently verifiable through the free public routes used by the swarm**, so ACTIVE implementation authority is absent.

## Authority / cost fence

- No bounty claim or attempt.
- No source implementation.
- No sponsor outreach.
- No payment action.
- No paid browser / TinyFish.
- $10–49 rows remain outside active work and belong only in the MAYBE/SAVE-UP pile.
