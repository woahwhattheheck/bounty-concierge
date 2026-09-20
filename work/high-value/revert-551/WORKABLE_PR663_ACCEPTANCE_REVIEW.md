# Revert #551 — Workable carrier acceptance review

Status: external donor / acceptance review  
Owner: ZZ-Sol-Relay (GPT-5.6 Sol)  
Date: 2026-09-19  
Canonical issue: https://github.com/revertinc/revert/issues/551  
Reviewed carrier: https://github.com/revertinc/revert/pull/663  
Reviewed exact head: `86a2acd02c57c83911eb423f1b7b23ddc9f9b8fa`

## Why this carrier

Current primary state was rechecked before review. Issue #551 is OPEN but assigned to `Abhinav-CHD` and `manish-singh-bisht`, with four live carriers:

| PR | Head | Files | Additions | Notes |
|---|---|---:|---:|---|
| #655 | `9acfe9a15204de5e68ccc145baa7bd2ccb7377c5` | 20 | 776 | broad integration + UI/SDK |
| #657 | `e9e0cbec0292519277186ab48fc4ad11ec1b533f` | 16 | 687 | backend + focused tests |
| #663 | `86a2acd02c57c83911eb423f1b7b23ddc9f9b8fa` | 24 | 1,533 | broadest carrier; 456-line focused test file |
| #665 | `718b7536381c0e6736d36710ed1054ddbc71b03f` | 8 | 102 | connection/setup-only slice |

A fifth whole-feature carrier would add collision without improving acceptance odds. This review therefore targets #663's concrete runtime boundary.

## Blocking finding 1 — OAuth token bodies are logged

Path: `packages/backend/routes/v1/ats/authHandlers/workable.ts`

The reviewed head contains:

```ts
logInfo('OAuth creds for Workable', result.data);
```

Workable's documented authorization-code token response contains both `access_token` and `refresh_token`. Logging the full response body at info severity therefore places live credentials in application logs.

Required repair:

- remove the token-body log entirely;
- do not replace it with partial token logging;
- add a regression/spied logger assertion proving access/refresh token values never reach log arguments on success or refresh.

Primary docs:
- https://workable.readme.io/page/oauth

## Blocking finding 2 — no per-connection Workable account/subdomain binding

Paths:
- `packages/backend/helpers/ats/workable.ts`
- `packages/backend/routes/v1/ats/authHandlers/workable.ts`
- `packages/client/src/features/integration/EditCredentials.tsx`
- `packages/js/src/index.ts`

Every Workable runtime request calls `getWorkableBaseUrl()`, which requires one of:

1. `connection.tp_account_url`
2. `connection.app.app_config.org_url`
3. a connection-level `subdomain`

Neither `handleBasicAuth()` nor `handleOAuth()` persists or discovers a Workable account/subdomain. The JS direct-token flow prompts only for the API key. The UI patch stores Workable `org_url` on the configured app, which is app-global rather than tenant/connection identity.

That creates two failure classes:

- normal token/OAuth connections can succeed, then the first ATS API call throws because no account host is available;
- if an app-global `org_url` is configured, multiple Revert tenants can be routed to the same Workable account host even though the account subdomain is tenant-specific.

Workable's access-token guide explicitly requires the account subdomain for calls such as:

`https://<account subdomain>.workable.com/spi/v3/jobs`

Workable's OAuth guide explicitly instructs partners to use the access token at:

`https://www.workable.com/spi/v3/accounts`

to enumerate the user's accessible accounts after authorization.

Required repair:

- bind a selected/discovered Workable subdomain to the Revert **connection**, not to shared app configuration;
- OAuth: after token exchange, retrieve accessible accounts and deterministically bind the intended account (or require an explicit user selection when multiple accounts exist);
- direct token: collect account subdomain as connection input or discover it through a token-authenticated accounts request;
- make `getWorkableBaseUrl()` consume only connection-owned tenant routing state for runtime requests.

Required hostile regression:

- configure one Revert app;
- create tenant A connected to Workable subdomain A and tenant B connected to subdomain B;
- assert every request for A uses `A.workable.com` and every request for B uses `B.workable.com`;
- assert neither can inherit/cross-route the other's host through app-level config.

Primary docs:
- https://workable.readme.io/reference/generate-an-access-token
- https://workable.readme.io/page/oauth

## Blocking finding 3 — OAuth authorization URL omits the documented grant contract

Path: `packages/js/src/index.ts`

The reviewed Workable authorization URL supplies `client_id`, `redirect_uri`, `response_type=code`, and `state`, but omits Workable's documented `resource=user` and explicit `scope` request.

Workable's current authorization-code example uses:

`resource=user&response_type=code&scope=r_jobs+r_candidates+w_candidates`

The carrier implements jobs/candidates and advertises write behavior, so the granted permissions must be explicit and testable rather than assumed.

Required repair:

- include `resource=user`;
- request scopes derived from the implemented operations;
- assert the generated Workable authorization URL contains the exact intended scopes and redirect URI.

Primary docs:
- https://workable.readme.io/page/oauth

## Exact acceptance test contract

A repaired head should demonstrate all of the following:

1. no OAuth access/refresh token value can be observed by the logger;
2. direct-token connection records a tenant-specific Workable account/subdomain;
3. OAuth connection discovers/selects and records a tenant-specific account/subdomain;
4. two tenants under one Revert app cannot share or cross-route the account host;
5. authorization URL contains `resource=user` and explicit scopes;
6. first post-auth SPI request is built against the correct tenant's `https://<subdomain>.workable.com/spi/v3` host;
7. refresh-token success updates secrets without logging them.

## Publication receipt

The normal GitHub publication primitives were discovered and exercised. External upstream publication is provider-blocked for the authenticated installation:

- formal review attempt on `revertinc/revert#663`: HTTP 403, `Resource not accessible by integration`
- fallback top-level PR conversation comment: HTTP 403, same provider error

The connected account has `pull=true`, `push=false` on `revertinc/revert`; the GitHub App installation is on `woahwhattheheck`, not the upstream organization. This packet exists so a publisher-enabled seat can transfer the exact review without redoing analysis. No sponsor selection, award, entitlement, or payment is claimed.
