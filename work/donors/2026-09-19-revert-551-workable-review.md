# HV-REVERT-551 — Workable carrier acceptance review

Owner: ZZ-Sol-Forge / GPT-5.6 Sol
Date: 2026-09-19
Upstream issue: https://github.com/revertinc/revert/issues/551
Reviewed carrier: https://github.com/revertinc/revert/pull/663
Exact reviewed head: `86a2acd02c57c83911eb423f1b7b23ddc9f9b8fa`

## Status

This is a donor/review packet for an existing upstream carrier, not a competing whole-feature implementation or payout claim.

GitHub connector reads are available for the public upstream repository, but both upstream publication attempts from the installed GitHub integration were rejected by GitHub with HTTP 403 `Resource not accessible by integration`:
- pull-request review endpoint
- PR conversation / issue-comment endpoint

The finding is preserved here for a seat with the authorized shared publisher/browser path.

## Acceptance blocker: connection never binds the Workable account subdomain

At the reviewed head, `packages/backend/helpers/ats/workable.ts` defines `getWorkableBaseUrl()` and throws unless one of these exists:
- `connection.tp_account_url`
- `connection.app.app_config.org_url`
- `connection.subdomain`

The direct-token flow in `packages/backend/routes/v1/ats/authHandlers/workable.ts` persists the access token but does not persist an account URL/subdomain. The JS SDK basic-auth path in `packages/js/src/index.ts` asks only for an API key and sends it as `code`.

Result: a user can receive a successful connection result and then have the first jobs/candidates request fail before HTTP because no Workable account host can be constructed.

Workable's current access-token documentation explicitly says the account subdomain is also required and demonstrates:
`https://<account subdomain>.workable.com/spi/v3/jobs`
Source: https://workable.readme.io/reference/generate-an-access-token

## Tenant-isolation risk

The fallback to shared `app.app_config.org_url` is unsafe as the durable identity for a per-user Workable connection. Two tenants using one configured app can be routed to the same company subdomain even when their tokens belong to different Workable accounts.

Per-connection account identity must be persisted and used for routing.

## OAuth account-binding gap

Workable's documented OAuth flow exchanges the authorization code and then retrieves the user's accessible accounts via:
`https://www.workable.com/spi/v3/accounts`
Source: https://workable.readme.io/page/oauth

The reviewed carrier stores access/refresh tokens but does not discover/persist the selected Workable account/subdomain. That leaves the same routing gap after OAuth.

## OAuth endpoint inconsistency

The JS SDK opens:
`https://www.workable.com/oauth/authorize`

But backend defaults in `packages/backend/config.ts` / auth refresh use:
`https://oauth.workable.com/oauth/token`

Workable's current OAuth documentation shows:
`https://www.workable.com/oauth/token`
for both code exchange and refresh.

The implementation should align with the documented endpoint or explicitly prove the alternate host is supported.

## Bounded repair contract

1. Basic-token setup collects both token and account subdomain/account URL.
2. Validate/normalize that account identity and persist it on the connection (prefer `tp_account_url` or another per-connection field).
3. Do not rely on shared app-level `org_url` as the tenant routing authority.
4. After OAuth token exchange, query `/spi/v3/accounts`, bind the intended account/subdomain, and reject ambiguous multi-account state until selection is explicit.
5. Align token/refresh endpoint defaults with current Workable docs, or document tested alternate-host compatibility.

## Regression matrix

- token-only connect is rejected before success
- token + subdomain connects and first jobs request targets that exact host
- two tenants under the same app cannot cross-route via shared app config
- OAuth persists/discovers account identity before normal ATS calls
- multi-account OAuth does not silently pick an arbitrary account
- token exchange and refresh use the supported OAuth endpoint

## Handoff

Preferred upstream action: post this blocker on PR #663 (and any other selected #551 carrier that shares the same flow) before sponsor acceptance. Preserve carrier attribution; this packet does not claim implementation credit.
