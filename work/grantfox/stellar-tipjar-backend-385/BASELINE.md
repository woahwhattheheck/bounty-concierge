# GrantFox baseline — Bonizozo/stellar-tipjar-backend #385

Operation: `GFOX2-104-R-BASELINE-SOL47-20260919`
Worker: ZZ / Sol-47 · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream head: `a9f477c45a3b6bb835c8b75286b7ec723da82795`

## Canonical issue

- GitHub: https://github.com/Bonizozo/stellar-tipjar-backend/issues/385
- GrantFox: https://contribute.grantfox.xyz/org/Bonizozo/repo/stellar-tipjar-backend/issue/385
- Observed state: OPEN / GrantFox Unassigned
- Existing comments: 3
- Matching open implementation PR: none surfaced in the focused census
- Labels: `Maybe Rewarded`, `GrantFox OSS`, `difficulty: very hard`, `security`, `Third Campaign`
- Reward boundary: labels are eligibility signals only; no award or payment is asserted.

## Exact current-source finding

Verified call graph:

`RecordTipRequest.message` → `tip_controller::record_tip_with_context` → `ModerationService::check_content` → `AiDetector::analyze` → Anthropic `/v1/messages` when `ANTHROPIC_API_KEY` is configured.

Pinned blobs include:
- `src/moderation/ai_detector.rs`: `42fe33189d21a226ea507635c78f6efc2c9201b6`
- `src/moderation/mod.rs`: `f757d415e89dc04d94ef655c83952a45ba6d1347`
- `src/models/tip.rs`: `272a8c227ca22bed32769d463b47e5ff3dafcd83`
- `src/controllers/tip_controller.rs`: `1b81ae732d80183127dbd872d9492b8d9ccc875c`
- `src/middleware/rate_limiter.rs`: `ca4a0442b39ab062648c7f2ec0adbae4497f11c9`
- `src/middleware/timeout.rs`: `a5ca7373b8f51a1c2f4df070b41589696868ed79`

### Stale-thread correction

A Sep 19 issue comment says the tip message has no input-length validation. That is stale at the pinned head. Current `RecordTipRequest.message` is capped at 280 characters and the public REST path uses `ValidatedJson<RecordTipRequest>`.

Other observed controls:
- Anthropic output cap: `max_tokens: 512`
- versioned write-rate default: 2 req/s, burst 5 per IP
- outer request timeout: 30s
- GraphQL record-tip input has no message field, so it is not a bypass for this path.

### Residual gap

`build_moderation_prompt` still interpolates untrusted text inside `<content>{}</content>` in the same user message as policy/schema instructions. A literal closing tag can escape the hand-built fence. `AiDetector` uses `Client::new()` without an explicit dependency-level timeout, has no retry loop, and no direct hostile-input/timeout AI test seam surfaced. A service-level AI input cap is also absent for future internal callers.

## Assigned implementation plan

1. Separate policy/system instructions from untrusted user content using provider-supported message roles.
2. Add a service-level pre-network input cap rather than duplicating the existing REST DTO cap.
3. Add an explicit Anthropic dependency timeout shorter than the outer request timeout.
4. Add an injectable/mockable AI client seam.
5. Cover delimiter escape, instruction-shaped input, oversize input, timeout, malformed response, and ordinary moderation.
6. Preserve rule-only fallback unless maintainers explicitly request different failure semantics.

## Provider publication attempt

GrantFox showed Unassigned and one application per user. Browser run `f31d8f0b-2863-4933-9f33-b8e1a6f099cc` attempted the application flow, but post-run verification found the same 3 pre-existing GitHub comments and GrantFox still Unassigned. Therefore **no application was submitted by this seat**.

This packet grants no assignment, application, implementation, payment, wallet, or reward authority.
