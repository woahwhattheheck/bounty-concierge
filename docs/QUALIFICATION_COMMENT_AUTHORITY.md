# Qualification comment authority

`concierge.bounty_qualification` is a fail-closed dispatch boundary. A normalized snapshot may include issue/body terms and, for direct callers, raw GitHub comments. Raw comments must not become paid-work authority merely because a caller copied an issue thread into JSON.

## Trust boundary

For the optional `comments` field, every entry must be an object with:

- `body`: string
- `author_association`: non-empty string from the canonical GitHub comment payload

Only comments whose association is `OWNER`, `MEMBER`, or `COLLABORATOR` are treated as authoritative contribution terms. The comparison is case-insensitive. Other comments remain untrusted discussion and are ignored for private-context rejection.

Bare comment strings and comment objects without usable authority metadata fail closed with `QualificationInputError`; silently guessing authority would be unsafe.

## Why this exists

The qualification gate rejects paid work whose authoritative contribution terms demand private execution context such as system/developer prompts, hidden instructions, session context, or conversation history. Without an authority boundary, any outside commenter could write one of those phrases and turn an otherwise actionable bounty into a terminal `REJECT`. That creates a denial-of-service surface for competing contributors and noisy issue threads.

The direct result exposes only safe counters:

- `trusted_comment_count`
- `ignored_untrusted_comment_count`

Raw comment text is never copied into the result.

## Preferred live path

For live GitHub claims, prefer `concierge.bounty_preflight.preflight_bounty()`. Preflight already reads canonical author-association metadata itself, reduces outside comments to competition signals, and revalidates issue/comment generation before dispatch. This document covers callers that use `qualify_dispatch()` or `python -m concierge.bounty_qualification` directly with normalized JSON snapshots.
