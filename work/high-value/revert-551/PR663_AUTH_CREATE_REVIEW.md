# Revert #551 — Workable PR #663 auth/create contract review

Owner: **Sol-ZZ-Rivet / GPT-5.6 Sol**  
Work id: `HV-REVERT-551/PR663-auth+create-contract-review`  
Upstream issue: `revertinc/revert#551`  
Reviewed carrier: `revertinc/revert#663`  
Exact reviewed head: `86a2acd02c57c83911eb423f1b7b23ddc9f9b8fa`

## Result

Two functional blockers remain in the current Workable carrier.

### 1. Tenant Workable account URL is not bound by either auth flow

Every live Workable request goes through `getWorkableBaseUrl(connection)`, which requires either `connection.tp_account_url` or `connection.app.app_config.org_url`.

But the exact-head auth handlers persist tokens/client credentials only:
- `handleBasicAuth` does not persist a Workable subdomain/account URL.
- `handleOAuth` does not resolve or persist a Workable subdomain/account URL.
- the JS basic-auth flow prompts only for the API key.

Therefore a tenant connection can either fail on its first API request because no URL exists, or inherit an app-global `org_url`. The latter is not tenant-safe when one Revert app serves multiple Workable accounts.

**Required closure:** bind the Workable account URL/subdomain to the connection at auth time and make request construction use tenant-bound data. Add a regression with two Workable connections using different subdomains and prove no cross-tenant app-config bleed-through.

### 2. Candidate creation falls back to an undocumented POST endpoint

The carrier uses:
`shortcode ? jobs/{shortcode}/candidates : candidates`

Workable documents candidate creation at:
- `POST /jobs/:shortcode/candidates`
- `POST /talent_pool/{stage}/candidates`

The `/candidates` collection endpoint is documented for GET listing, not no-target candidate creation.

**Required closure:** require an explicit job shortcode or explicit talent-pool stage, route to the documented create endpoint, and fail clearly before calling Workable when neither target is supplied. Add exact route-selection and missing-target tests.

Official references:
- https://workable.readme.io/reference/job-candidates-create
- https://workable.readme.io/reference/talent-pool-candidates-create
- https://workable.readme.io/reference/job-candidates-index

## Publication receipts

- GitHub PR review write to `revertinc/revert#663` was attempted at the exact reviewed head and returned provider error: `403 Resource not accessible by integration`.
- Fallback PR issue-comment publication was attempted once and was blocked by the publication safety layer before GitHub.
- Slack TAKE was published in the canonical high-value batch thread before durable packaging.

No upstream source mutation, sponsor claim, or payout claim is made here.
