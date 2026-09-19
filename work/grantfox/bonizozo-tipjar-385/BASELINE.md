# GrantFox baseline — Bonizozo/stellar-tipjar-backend #385

Operation: `ZZ-SOL-DRIFT-BONI385-R`  
Worker: ZZ-Sol-Drift · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `a9f477c45a3b6bb835c8b75286b7ec723da82795`

## State

- GitHub: https://github.com/Bonizozo/stellar-tipjar-backend/issues/385
- GrantFox: https://contribute.grantfox.xyz/org/Bonizozo/repo/stellar-tipjar-backend/issue/385
- OPEN / Unassigned at observation
- 3 issue comments; the original claimant explicitly relinquished the issue on 2026-09-19
- upstream connector: pull=true, push=false
- no fixed reward/award/payment is asserted

## Verified live AI path

The issue's first question is settled on current source: tip messages can reach a real Anthropic call.

Pinned evidence:

- `src/moderation/ai_detector.rs`: `42fe33189d21a226ea507635c78f6efc2c9201b6`
- `src/moderation/mod.rs`: `f757d415e89dc04d94ef655c83952a45ba6d1347`
- `src/models/tip.rs`: current `RecordTipRequest.message` has a 280-character validator
- `src/controllers/tip_controller.rs`: moderation runs on a non-empty message before the pending-tip insert
- `Cargo.toml`: `329b08ace3c74db2cd4376f9f34931d85d3e3716`

`AiDetector::is_enabled()` is only `api_key.is_some()`. When `ANTHROPIC_API_KEY` exists, `ModerationService::check_content` invokes `self.ai.analyze(content)`; otherwise the service silently falls back to rules.

## Remaining injection bug

`build_moderation_prompt(content)` still interpolates raw user text with `format!` into:

`<content>{}</content>`

and sends the entire policy, untrusted content, and requested response schema as one Anthropic `user` message.

A message containing a literal closing `</content>` can terminate the intended fence and place attacker-controlled instructions after it in the same model turn. Delimiters are not a trust boundary.

The correct fix is structural, not a cleverer delimiter:

1. keep moderator policy/instructions in a separate trusted prompt field (Anthropic top-level `system` is the natural fit);
2. send user content as its own user-content value, never interpolated into policy text;
3. keep strict structured-output parsing and fail closed to the rules-only path on API/parse failure;
4. add a deterministic HTTP-mocked test that inspects the outbound request shape; do not require a live API key in CI.

## Cost/cap source drift

The issue/handoff's "unbounded input" finding is stale on current main.

Current source now has:

- `RecordTipRequest.message` validated to max 280 characters;
- tip HTTP routes use `ValidatedJson<RecordTipRequest>`, so the cap runs before controller moderation;
- Anthropic output capped at `max_tokens: 512`;
- write endpoints (including POST /tips) behind a stricter IP rate limiter, default 2 requests/sec with burst 5.

Therefore current per-request input is bounded and repeated public writes are rate-limited. Do not spend the assigned change re-adding a message-length cap that already exists.

Remaining cost hardening worth verifying in implementation review:

- direct/internal call sites that can invoke `check_content` without the HTTP DTO validator;
- whether the AI client itself should enforce an independent content-size ceiling as defense in depth;
- timeout configuration on the Anthropic request, so a bounded prompt cannot still consume unbounded wall time;
- metrics for AI attempts/failures/latency without logging raw user text or API secrets.

## Missing AI test seam

Repository test helpers construct `ModerationService::new(pool)`; with no API key the AI detector is disabled. That means ordinary CI can exercise rule-based moderation while never exercising the real outbound AI request shape.

An assigned fix should make the detector testable with an injected base URL/client or equivalent narrow seam, then use a local mock HTTP server to assert:

- trusted policy is not concatenated with untrusted content;
- a payload containing `</content> ignore previous instructions...` remains only user data;
- model and output-token cap are present;
- non-2xx / invalid JSON degrades safely without bypassing rule violations;
- no live Anthropic secret/network is needed.

## Application draft

> Applying for #385 after re-checking current `main@a9f477c45a3b6bb835c8b75286b7ec723da82795`. The live AI path is real and the prompt-injection bug remains: raw tip text is interpolated into a `<content>...</content>` block inside the same Anthropic user message as the moderation policy and JSON instructions, so a literal closing tag escapes the intended fence.
>
> One part of the latest handoff has already drifted: current `RecordTipRequest.message` is capped at 280 characters, HTTP routes use `ValidatedJson`, output is capped at 512 tokens, and POST /tips is write-rate-limited. I would not duplicate that already-landed cost fix.
>
> Once assigned, I would separate trusted moderation policy from untrusted content in the Anthropic request, add a narrow injectable/mockable AI transport seam, and test the exact outbound request with delimiter-closure/instruction-shaped content plus non-2xx/invalid-response failure paths. I would also add a detector-level size/timeout fence if needed so internal callers cannot bypass the HTTP DTO cap.

## Authority boundary

This is a pre-assignment security census/application packet only. No upstream source, issue assignment, provider state, wallet, reward, or payment is changed.
