# GrantFox source baseline — Stellar-MarkeyPay/Stellar-MarketPay #162

Operation: `GFOX2-20260919-100/R-BOLA-source-baseline`  
Worker: ZZ-Sol-Rook · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `42250890ecb5b76228675167f47452e54fb28977`

## Canonical issue and authority

- GitHub: https://github.com/Stellar-MarkeyPay/Stellar-MarketPay/issues/162
- GrantFox: https://contribute.grantfox.xyz/org/Stellar-MarkeyPay/repo/Stellar-MarketPay/issue/162
- Provider state at census: Unassigned, one visible prior application, `Maybe Rewarded` / `GrantFox OSS`
- No fixed award, payment, or assignment is established by this packet.
- This is a pre-assignment source audit only; no upstream source or provider state was mutated.

## Current-main authorization findings

### Applications router

Pinned blobs:

- `backend/src/routes/applications.js` — `1f1248b7fec9a2dd3199988da8fc651bbd20da9d`
- `backend/src/services/applicationService.js` — `1749c23373c02c97eadc1673c4e96369f55ea961`
- `backend/src/server.js` — `c31c2520c2f154056f5712c793e07c246bfc6d43`

`server.js` mounts `/api/applications` without a router-wide `verifyJWT` guard. The applications router has eight routes; only `POST /:id/reputation-proof` uses `verifyJWT`.

State-changing routes that accept actor identity from request data include:

- `POST /` — freelancer identity is supplied in the application payload.
- `POST /job/:jobId/close-bidding` — passes `req.body.clientAddress`.
- `POST /:id/reveal` — passes `req.body.freelancerAddress`.
- `POST /:id/accept` — passes `req.body.clientAddress`.
- `DELETE /:id` — passes `req.body.freelancerAddress`.

The service layer then compares those supplied addresses to the stored resource owner before mutating. Examples include `job.clientAddress !== clientAddress` and `app.freelancer_address !== freelancerAddress`. That proves equality to an asserted address, not control of that identity.

A post-assignment repair should derive the actor from the authenticated principal and use body addresses, if retained at all, only as consistency checks.

### Profiles router

Pinned blob:

- `backend/src/routes/profiles.js` — `e4cabe46e9553063150e5f9367ddd62beb4a1d99`

Current unauthenticated mutations include profile upsert and routes changing notification, availability, price-alert, and endorsement state. The notification settings read is also unauthenticated and returns email plus webhook URL (with secret masked), so public/private read policy needs an explicit decision rather than an indiscriminate auth change.

Most importantly, the file defines `POST /:publicKey/endorse` twice:

1. an earlier unauthenticated handler accepts `endorserAddress` from the request body and sends the response;
2. a later handler correctly uses `verifyJWT`, derives `req.user.publicKey`, and requires a completed-job relationship.

Under normal Express routing, the earlier identical method/path handler responds without calling `next()`, so the later authenticated handler is shadowed.

### Repo-local good patterns

- `backend/src/routes/savedSearches.js` — `997b43dcac4ede0bbcb17cc4c149b1bf9ec89f4f`
- `backend/src/routes/messageRoutes.js` — `b0472271301d8eb0c8f3a8ee5cbb3a6c1daf0f0b`

These already use `verifyJWT` and `req.user`, providing a local pattern for the shared owner assertion requested by #162.

Fresh source/issue search found no existing application-auth/BOLA repair, and the backend test-tree census found no application-named route test file.

## Assignment-ready acceptance map

After provider/maintainer assignment:

1. authenticate private/user mutations and derive the actor from `req.user.publicKey`;
2. add a shared resource-owner assertion rather than repeating body-address equality;
3. remove or make unreachable the shadowing unauthenticated endorsement handler;
4. classify public GET visibility separately from write authorization;
5. add negative cross-account tests for submit, close-bidding, reveal, accept, withdraw, notification read/write, profile overwrite, availability, price alerts, and endorsement;
6. keep deliberately public listing reads public only where policy says so.

No live-service exploit was performed. This packet records static current-source evidence only.
