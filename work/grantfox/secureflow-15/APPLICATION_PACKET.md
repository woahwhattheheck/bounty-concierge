# Secureflow #15 — source-specific application packet

Use only after a fresh assignment/application census confirms this lane is still eligible.

## Draft

I'd like to take Secureflow #15. I reviewed current `main@5a52f4ad2c6da8d29bc4a2ebb65d904251ab81c6` rather than treating the issue text as the source of truth.

The current backend still protects `/v1/*` with one static `API_SECRET`, and when that secret is unset the middleware deliberately allows requests through. More importantly, the message and notification routes take the wallet identity from body/query fields: a caller can choose `sender_address`, inbox/read `wallet`, or notification wallet without a verified Stellar principal.

My implementation would keep service authentication separate from wallet authentication and add a narrowly scoped challenge-response flow for wallet-owned resources. The challenge would use a random, short-lived, single-use nonce bound to the exact public key plus network/domain/purpose; verification would use the repo's existing Stellar SDK and consume the nonce atomically. A one-hour JWT would carry the verified wallet as its subject with fixed algorithm/issuer/audience checks. Message sender and owner-only reads/marks would then derive identity from the verified request principal rather than trusting caller-selected addresses.

I would also keep notification creation explicitly service-authorized unless maintainers want a different authority model, so adding wallet auth does not accidentally widen write privileges.

Tests would cover valid login plus wrong signer/address/network/domain, nonce replay/expiry, malformed signatures, expired/tampered JWTs, unauthenticated 401s, sender spoof attempts, cross-wallet reads/marks, and successful owner access without requiring live Supabase or a browser wallet extension. Frontend expiry handling would obtain a fresh challenge/signature instead of silently creating an unrequested long-lived refresh-token mechanism.

If assigned, I would re-census current `main`, open PRs, and route semantics before touching source so the patch composes with any intervening backend-hardening work.
