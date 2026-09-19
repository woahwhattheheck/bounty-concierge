# Creditra-Backend #300 — source-specific application packet

Use only after a fresh GrantFox/GitHub census confirms #300 is still unassigned and has no active implementation carrier.

## Draft

I'd like to take Creditra/Creditra-Backend #300. I audited current `main@70189f4a6e52f53c764123df760c2a1de39876ce` and mapped the actual credit mutation surface before proposing the security change.

The current router has seven credit-line mutations. Five — create, update, delete, draw and repay — have no auth middleware. Suspend and close use the single `X-Admin-Api-Key` secret. More importantly, the repo does not currently have an authenticated borrower/lender principal: generic API-key and admin-key middleware validate secrets but attach no subject/role/tenant identity, and the tenant rate limiter derives its tenant bucket from caller-controlled `x-tenant-id`, which must not become an authorization signal.

My approach would be:

1. Define one trusted request principal contract and map existing admin/integration credentials into explicit roles. For borrower/lender identity, use the maintainer-approved trust mechanism rather than trusting `walletAddress` from the body or path.
2. Define a declarative action matrix covering all seven credit mutations, with role and ownership/tenant predicates separate, and enforce it in shared middleware before handlers mutate or reveal resource state.
3. Bind audit records to the authenticated subject/role/tenant. Today draw/repay use caller-supplied wallet data as audit actor, CRUD has no audit principal, and privileged transitions do not identify the actual operator.
4. Add a route-inventory test that fails when a mutation lacks exactly one policy entry, plus negative matrix tests for unauthenticated, wrong-role, wrong-tenant/owner, service-role misuse, unknown target, and non-disclosure parity between existing and nonexistent cross-tenant targets.

I would preserve intentionally public reads unless maintainers expand scope, update OpenAPI/security docs atomically for newly protected mutations, and keep rate-limit tenant identity separate from security identity.

Estimate after assignment: first draft within one working day, with the policy/identity seam and inventory tests landed before changing individual mutation behavior.

I'll wait for assignment before coding, as requested by the issue.
