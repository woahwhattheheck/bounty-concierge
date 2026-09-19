# Independent residual review — stellar-tipjar-backend #385

Reviewer: **ZZ-Sol-Fennec / GPT-5.6 Sol**  
Pinned upstream: `Bonizozo/stellar-tipjar-backend@a9f477c45a3b6bb835c8b75286b7ec723da82795`  
Parent packet: `work/grantfox/stellar-tipjar-backend-385/BASELINE.md`

This addendum does not replace the landed baseline. It records one source-level response-semantics gap that the parent packet did not capture.

## Finding: structured `is_flagged` verdict is parsed but discarded

Pinned `src/moderation/ai_detector.rs` blob:
`42fe33189d21a226ea507635c78f6efc2c9201b6`.

The Anthropic response schema includes:

```rust
struct ModerationResponse {
    is_flagged: bool,
    score: f32,
    reasoning: String,
    violations: Vec<AiViolation>,
}
```

but `parse_moderation_response()` converts the parsed response into `AiDetectionResult` using only:

- `score`
- `reasoning`
- `violations`

The `is_flagged` value is not propagated.

Pinned `src/moderation/mod.rs` blob:
`f757d415e89dc04d94ef655c83952a45ba6d1347`.

`ModerationService::check_content()` then derives the service verdict from accumulated violations or `ai_score > 0.7`.

Therefore a syntactically valid model response with `is_flagged: true`, an empty violations array, and a score at or below the service threshold does **not** produce a flagged moderation result. This is not a prompt-injection exploit by itself; it is an internal contract mismatch between the requested structured response and the decision logic.

## Assignment-time closure

After official provider/maintainer assignment, the #385 implementation should make the response contract unambiguous:

1. Either propagate `is_flagged` into `AiDetectionResult` and define how it composes with score/violations, **or** remove it from the requested/parsed schema and document score/violation fields as the only decision authority.
2. Add deterministic parser/service tests for contradictory structured responses:
   - `is_flagged=true`, low score, no violations;
   - `is_flagged=false`, high score;
   - `is_flagged=false`, explicit high-confidence violation;
   - malformed or unknown violation type.
3. Keep the already-landed 280-character REST message bound and the parent packet's prompt-boundary / mock-AI test plan.
4. Do not use a paid live model call as the ordinary regression oracle.

## Authority fence

Review evidence only. No application, assignment, upstream implementation, reward, award, wallet, or payment authority is created here.
