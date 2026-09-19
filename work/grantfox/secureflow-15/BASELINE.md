# Secureflow #15 — Stellar signature-auth baseline

Status: source-audited, application/assignment required before upstream implementation.

## Target

- Upstream: `Secureflow-protocol/secureflow`
- Issue: #15 — **[Backend] Stellar signature auth to protect private API routes**
- Upstream source generation: `main@5a52f4ad2c6da8d29bc4a2ebb65d904251ab81c6`
- GitHub issue state at census: OPEN, unassigned, 0 comments
- Exact closing-PR census: 0 for `Closes #15`, 0 for `Fixes #15`
- Exact Slack repo/#15 census at publication: 0 prior fleet messages
- Upstream permission from the connected account: pull=true, push=false
- Reward truth: issue is labeled Maybe Rewarded / GrantFox OSS / Official Campaign; no cash amount is asserted here.

This packet is a durable pre-assignment source census. It performs no upstream mutation, GrantFox application, wallet action, payout change, or reward claim.

## Current security gap

The issue's core premise still matches current `main`.

### Global route gate is a static bearer secret, not wallet identity

`backend/src/index.ts` (blob `55c811abfcdea34737454aad8962ed3d31486547`) wraps all `/v1/*` routes in `requireApiSecret(apiSecret)`. If `API_SECRET` is unset, the middleware deliberately allows the request and logs that the routes are open.

`backend/src/middleware/auth.ts` (blob `131126f4d9429717dc2ec7e488ecfd24013ee38d`) only compares `Authorization` to the literal `Bearer ${API_SECRET}`. It does not establish a Stellar wallet principal, issue a challenge, verify a wallet signature, or mint/verify a user JWT.

### Message ownership is caller-selected

`backend/src/routes/messages.ts` (blob `6614f3238feca854f2c9e11e6b18646ecc20447f`) accepts wallet identity from request fields:

- POST `/v1/messages`: trusts `sender_address` after format validation.
- GET `/conversation`: accepts arbitrary `a` and `b` query addresses.
- GET `/inbox` and `/unread-count`: accept arbitrary `wallet` query addresses.
- PATCH conversation/single-message read endpoints accept arbitrary `wallet` query addresses.

There is no request principal binding the sender/read target to a verified wallet.

### Notification ownership is caller-selected

`backend/src/routes/notifications.ts` (blob `a633e4fb51c752da556f8d046fc19d10879fe9d7`) reads and marks notifications by caller-supplied wallet query value. The POST path accepts an arbitrary `wallet_address`.

### Runtime dependencies

`backend/package.json` (blob `ea044fcd675e0c38c65daabeab1ba2b6ff08a3e8`) already has `@stellar/stellar-sdk@14.2.0`, Express, and Supabase. It does not currently declare a JWT library or backend test runner.

## Source-aligned implementation seam

A strong implementation should avoid replacing the existing server-to-server API-secret role indiscriminately. The wallet-auth boundary belongs on wallet-owned resources while internal/service routes retain an explicit service-auth contract.

Recommended architecture:

1. **Challenge store + canonical challenge**
   - issue a cryptographically random single-use nonce with a short expiry;
   - bind the challenge to the exact Stellar public key, network/domain, purpose, and expiry;
   - consume the nonce atomically on successful verification;
   - reject reused, expired, malformed, wrong-address, and wrong-domain/network challenges.

2. **Signature verification**
   - use the existing Stellar SDK's Ed25519 primitives against the claimed public key;
   - sign/verify one precisely documented byte representation, not a loosely reconstructed string;
   - do not accept a body address as identity after verification.

3. **JWT session**
   - mint a short-lived token with the verified wallet as the subject;
   - enforce an explicit algorithm, issuer, audience, issued-at, expiry, and maximum one-hour lifetime;
   - keep the JWT signing secret distinct from the legacy `API_SECRET`;
   - inject a typed request principal such as `req.auth.wallet`.

4. **Owner-bound routes**
   - derive POST message sender from the authenticated wallet;
   - prevent cross-wallet inbox/conversation/read access;
   - bind notification reads/marks to the authenticated wallet;
   - explicitly decide whether notification creation remains a service-only API-secret operation rather than accidentally granting user write authority.

5. **Frontend lifecycle**
   - obtain a challenge, sign through the existing wallet integration, exchange it for a JWT, and retry/refresh via a fresh signed challenge when the one-hour token expires;
   - do not invent a long-lived refresh token unless maintainers explicitly choose that design.

6. **Deterministic tests**
   - challenge issuance + canonical bytes;
   - valid verification;
   - wrong signer/address/network/domain;
   - nonce replay and expiry;
   - malformed signatures;
   - expired/tampered/wrong-audience JWT;
   - unauthenticated 401;
   - cross-wallet access denial;
   - sender spoof prevention;
   - successful owner access;
   - no mandatory live Supabase, wallet extension, or network dependency.

## Issue acceptance map

The current issue explicitly requires:

- `GET /auth/challenge?address=G...`
- client wallet signature
- `POST /auth/verify` -> JWT
- protected message sender, inbox owner, and notification owner paths
- unauthenticated requests -> 401
- JWT expiry after one hour
- frontend refresh on expiry
- tests for challenge issuance, verification, and expired JWT

The implementation plan above preserves those requirements while closing replay and cross-wallet authorization gaps that would otherwise survive a superficial challenge/JWT implementation.

## Assignment fence

Do not begin upstream source implementation from this packet alone. Re-check GrantFox/GitHub assignment and active PR state immediately before implementation. Upstream is pull-only for the connected account, so an assigned implementation would additionally require an authorized fork/publication path.
