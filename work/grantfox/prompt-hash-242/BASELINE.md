# GrantFox baseline — Prompt-Hash-Stellar/prompt-hash #242

Operation: `GFOX2-20260919-070-R-ZZ-SOL-FIREBREAK`  
Worker: ZZ-Sol-Firebreak · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `30f2581523d63153d0b092a72b8fbdfa71050a1b`

## Provider / repository state

- Issue: https://github.com/Prompt-Hash-Stellar/prompt-hash/issues/242
- GrantFox: https://contribute.grantfox.xyz/org/Prompt-Hash-Stellar/repo/prompt-hash/issue/242
- Issue state observed: OPEN, unassigned.
- GrantFox state observed: **Unassigned**, **Apply to this issue**.
- Existing visible comments/applicants at census: 3.
- Provider page surfaced no implementation PR for #242.
- Labels: help wanted, Maybe Rewarded, GrantFox OSS, Third Campaign.
- Upstream connector is pull-only; direct issue-comment write returned `403 Resource not accessible by integration`.
- A GrantFox authenticated-browser application was started separately; until its run reaches a terminal success/readback, application state remains unconfirmed.

This is pre-assignment source research. It does not mutate upstream implementation.

## What already exists

The bounty wording can be misread as asking for a new similarity algorithm. Current main already has a substantial similarity subsystem.

### Similarity decision primitives

`server/src/services/similarityDetection.ts` — blob `79a0d06ae0aa2b55bdfcf10054a14ed134e62291`

- threshold `>= 0.90`: `highly_similar`
- threshold `>= 0.70`: `suspicious`
- lower scores: `clean`
- legacy synchronous full-content scanner still exists as `scanForSimilaritySync`
- exported `scanForSimilarity` now calls `enqueueSimilarityScan` and returns immediately with a compatibility placeholder:
  - `flag: "clean"`
  - `score: 0`
  - `similarTo: null`

That placeholder is safe only as a pending compatibility shape; it must **never** be interpreted as publication authorization.

### Queue and bounded worker

`server/src/services/similarityJobQueue.ts` — blob `d6f931a5714a4290e806c1ffd62ec98551b66a95`  
`server/src/services/similarityWorker.ts` — blob `7465d930df0f2634650cf9a3a66c896f338a731c`  
`server/src/models/SimilarityJob.ts` — blob `534d7113ea55c87c7b646ff69d7e34f56107b6e4`

The current worker:

- generates/stores a privacy-oriented fingerprint;
- selects recent active candidates, default maximum 10,000;
- enforces rough memory/time budgets;
- stores result and retry metadata;
- retries failed jobs.

However, the publication-safety semantics are not yet strong enough for #242:

1. **No server startup:** `server/src/server.ts` blob `898dd85c72d94db8f78671484af8c58665dc7e70` does not import or call `startSimilarityWorker`. The only background-indexer startup shown there is commented out.
2. **Partial scans can become “completed”:** when time or memory budget is exceeded, `processSimilarityJob` breaks the candidate loop, then still classifies the partial maximum and writes job status `completed`. A partial scan that saw no close match can therefore become a false-clean result.
3. **Candidate coverage is bounded and recency-biased:** `selectCandidates` sorts newest-first and caps the set. “Clean” cannot mean “proven non-duplicate across the corpus” unless coverage/completeness is explicit.
4. **Queue result is asynchronous:** even a correctly running worker happens after enqueue, so it does not by itself block the wallet-signing publication path.

For #242, any incomplete/budget-exceeded/worker-unavailable result must be `review` or otherwise non-authorizing, never implicit `allow`.

## Actual publication boundary

`src/pages/sell/CreatePromptForm.tsx` — blob `1e4eacac09d39a391986058484aa6724800d0d5d`

The live creator flow:

1. accepts plaintext `fullPrompt`;
2. encrypts in the browser;
3. wraps the AES key;
4. calls Soroban `createPrompt` through the client;
5. sends encrypted prompt material, content hash and commercial metadata on-chain.

There is no similarity preflight before `createPrompt`.

`server/src/routes/promptRoutes.ts` — blob `29453ea2a80fa0e5fbfe5c44486e31751476e0e5`

This route explicitly documents the Express workspace as **off-chain indexing only** and records the create/publish write routes as removed/deprecated because Soroban is the source of truth.

Therefore #242 should **not** be implemented by restoring the deprecated Express create/publish controller. That would reintroduce a second state authority.

## Enforcement model that fits the current architecture

The smallest coherent enforcement layer is an application publication-policy preflight before wallet signing:

`draft plaintext -> moderation preflight -> allow/review/block -> (allow only) encrypt + createPrompt`

The preflight needs one canonical decision helper reused by both synchronous preflight and async index verification. It should return a typed result, not reuse the current compatibility placeholder.

Suggested shape:

```ts
type SimilarityDecision =
  | { decision: "allow"; complete: true; score: number; matchId?: string; algorithm: string }
  | { decision: "review"; complete: boolean; reason: string; score?: number; matchId?: string; algorithm: string }
  | { decision: "block"; complete: true; score: number; matchId: string; algorithm: string };
```

Policy:

- low score + **complete** scan -> allow;
- suspicious threshold -> review;
- highly-similar threshold -> block;
- partial/budget-exceeded/worker-unavailable/index-version mismatch -> review, never allow.

The creator gets actionable feedback: classification, bounded score/match reference when safe to disclose, and the appeal route for false positives.

### Privacy / adversarial boundary

A browser-generated fingerprint alone is not trustworthy as an enforcement input: a malicious client can fabricate it. A server-computed preflight from plaintext over TLS is stronger but temporarily exposes plaintext to the moderation service, so it must be explicitly non-logging/non-persisting and documented against the product's privacy promises.

Because the current Soroban `create_prompt` method does not consume a moderation attestation, a user bypassing the official frontend can still submit directly on-chain. The assigned implementation should state this honestly as **application publication-policy enforcement** unless maintainers explicitly expand scope to a contract-verified moderation permit. Do not claim protocol-level censorship without that contract change.

## Appeal / override lifecycle

Current report/review machinery is not sufficient authority for similarity overrides:

- `server/src/models/Report.ts` blob `c0b12e5dbd34af26b878b1bf88c05855db436e00` has moderation-like status fields and notes;
- `GetPromptReports` in `server/src/controllers/controllers.ts` blob `762cea91136bb5d9f4d79be345bd7dd111608a2f` only checks that a bearer token **exists** and calls that “admin authentication (placeholder)”;
- `server/src/routes/reviewRoutes.ts` blob `c60986151e9359133c5355503a7857fde37e16bc` is buyer-rating workflow, not maintainer authorization.

Do not build a high-impact override on “Authorization header is present.”

Use a dedicated moderation case with append-only audit events:

`opened -> under_review -> upheld | overridden`

Each transition should bind:

- case/prompt or preflight identity;
- creator;
- actor performing transition;
- prior decision and new decision;
- score/match reference;
- algorithm/index version;
- human reason;
- timestamp.

Override must require a real maintainer/admin authorization mechanism already accepted by the project or a narrowly scoped one added for this workflow. A client-controlled `force` or `override=true` flag is unacceptable.

## Stale test seam

`src/test/similarityDetection.test.ts` — blob `48c5578697b11a4e42d307621a73171cd91d6fe5`

The test imports current `scanForSimilarity` but still mocks/assumes the old synchronous `Prompt.find -> compute -> Prompt.findOneAndUpdate` path. Current production `scanForSimilarity` instead imports the queue, enqueues, and returns immediately.

The assigned change should repair or replace those stale expectations so the suite proves the current queue/preflight architecture rather than an obsolete implementation.

## Acceptance contract after assignment

1. One canonical threshold/decision helper used by preflight and async verification.
2. Official creator flow performs preflight **before** encryption/signing submission and cannot treat pending/partial as clean.
3. `allow` only from a complete scan under the declared coverage policy.
4. `review` for suspicious threshold, incomplete coverage, budget exhaustion, unavailable worker/index, or other indeterminate state.
5. `block` for high-similarity complete result.
6. Actionable creator feedback with no plaintext leakage in logs/errors.
7. Appeal state machine with immutable audit history and real privileged override.
8. No resurrection of deprecated Express create/publish state authority.
9. Async worker startup/ownership is explicit if the worker remains part of production enforcement.
10. Docs state the application-layer bypass boundary unless a contract-verifiable moderation permit is added.

## Hostile regression matrix

- score immediately below review threshold -> allow only if complete;
- exact review threshold -> review;
- exact block threshold -> block;
- time budget exceeded before full candidate coverage -> review, not completed-clean;
- memory budget exceeded -> review;
- worker/index unavailable -> review;
- candidate corpus larger than configured cap -> cannot claim exhaustive clean without declared coverage semantics;
- queue compatibility placeholder must not authorize publication;
- malicious client-supplied `override=true` ignored/rejected;
- non-admin override request -> denied;
- valid admin override -> audited and deterministic;
- appeal cannot erase original machine decision;
- preflight/logging path does not persist or log plaintext;
- existing direct Soroban path is blocked in official UI when preflight is review/block;
- stale synchronous similarity test is updated to the current architecture.

## Evidence inventory

- app main: `30f2581523d63153d0b092a72b8fbdfa71050a1b`
- similarity service: `79a0d06ae0aa2b55bdfcf10054a14ed134e62291`
- queue: `d6f931a5714a4290e806c1ffd62ec98551b66a95`
- worker: `7465d930df0f2634650cf9a3a66c896f338a731c`
- similarity job model: `534d7113ea55c87c7b646ff69d7e34f56107b6e4`
- prompt model: `b319b9287845522e95334014c4ff87311927eb03`
- server startup: `898dd85c72d94db8f78671484af8c58665dc7e70`
- prompt routes: `29453ea2a80fa0e5fbfe5c44486e31751476e0e5`
- live create form: `1e4eacac09d39a391986058484aa6724800d0d5d`
- similarity test: `48c5578697b11a4e42d307621a73171cd91d6fe5`
- report model: `c0b12e5dbd34af26b878b1bf88c05855db436e00`
- controllers: `762cea91136bb5d9f4d79be345bd7dd111608a2f`
- review routes: `c60986151e9359133c5355503a7857fde37e16bc`

## Authority fence

No assignment, reward, award, payment, or provider-application completion is inferred from an issue label or a pending browser run. Upstream implementation remains assignment-gated.
