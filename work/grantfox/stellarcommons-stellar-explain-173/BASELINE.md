# GrantFox baseline — StellarCommons/Stellar-Explain #173

Operation: `GFOX3-20260919-stellar-explain-173-R-ZZ-SOL-DELTA`  
Worker: ZZ-Sol-Delta · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `5c1e18d1ed6e28734e8305a134c67613ed5d8e8c`

## Canonical issue/provider state

- GitHub issue: https://github.com/StellarCommons/Stellar-Explain/issues/173
- GrantFox listing: https://contribute.grantfox.xyz/org/StellarCommons/repo/Stellar-Explain/issue/173
- Issue state: OPEN
- GitHub assignee: none
- Existing issue/application comments: 1, generic
- Live GrantFox state: Unassigned; `Apply to this issue` visible; one application per user
- Connector permission snapshot: pull=true, push=false
- Exact issue-linked PR search: no carrier returned
- Public open-PR searches for `AssetList`, `balances AccountExplanation`, and `xlm_balance`: no matching open carrier surfaced
- Reward interpretation: `Maybe Rewarded` / campaign labels indicate possible eligibility only. No fixed award, assignment, or payment is asserted.

This is a pre-assignment source/data-flow packet. It does not modify the upstream repository.

## Current data flow at pinned main

The issue's high-level diagnosis is correct: the HTTP/UI account explanation still exposes only XLM balance plus the count of non-native trust lines. The underlying backend is farther along than the issue wording implies, though: the complete Horizon balance list is already fetched and retained in the domain model before the explainer discards most of it.

### Horizon response -> domain account

`packages/core/src/services/horizon.rs`  
Pinned blob: `6f9fd1c08ffb3afcc6b3328dcf02c44923f2b2ee`

Current private `HorizonBalance` already deserializes:

- `asset_type`
- optional `asset_code`
- optional `asset_issuer`
- `balance`
- `is_authorized`

`HorizonAccount::into_domain` maps every Horizon balance into the domain `Balance`.

Important residual: Horizon trustline `limit` is **not deserialized or retained**. Adding only an output field later would invent data; the repair must extend the ingestion/domain pipeline first.

### Domain model already owns the balance list

`packages/core/src/models/account.rs`  
Pinned blob: `b1205ddbce7be9d3342e4bf4ecbdf25f03cabd4a`

The existing domain `Balance` has:

- `asset_type: String`
- `asset_code: Option<String>`
- `asset_issuer: Option<String>`
- `balance: String`
- `is_authorized: bool`

`Account` already contains `balances: Vec<Balance>`.

This means issue #173 should **extend/reuse the existing domain balance pipeline**, not add a second competing raw account-balance store. Add the missing trustline `limit` at the Horizon/domain boundary, then create a deliberate presentation mapping for the API contract.

### Explainer collapses full balances to two summary fields

`packages/core/src/explain/account.rs`  
Pinned blob: `f97b3ac3e24e3e2bc39aac6afed96346ad2a47d0`

`explain_account_with_org_name`:

1. scans `account.balances` for `asset_type == "native"`,
2. returns that amount as `xlm_balance`,
3. filters non-native balances,
4. returns only their count as `asset_count`.

`AccountExplanation` has no `balances` field.

Current unit tests cover XLM extraction, other-asset count, home domain/org name, flags, and missing-XLM fallback. They do **not** assert full-balance mapping, native normalization, trustline limits, or stable ordering.

### HTTP response repeats the collapsed contract

`packages/core/src/routes/account.rs`  
Pinned blob: `657c0f11b256c40db5240c3b58006cc653ec349c`

`AccountExplanationResponse` independently lists:

- address
- summary
- xlm_balance
- asset_count
- signer_count
- home_domain
- org_name
- flag_descriptions

It does not expose `balances`. The route manually copies fields from the explainer, so an explainer-only change would still not reach clients.

Pinned integration fixture:
`packages/core/tests/integration/fixtures/account_explanation.json` blob
`1d7d0f3ad8f3dfb5ae0f8766955aaabbe06864cf`

The fixture likewise contains no `balances` array.

### Horizon tests do not currently protect account-balance mapping

`packages/core/src/services/horizon_test.rs`  
Pinned blob: `dd83f562d2b84d9766bddab9af35329b342999ea`

The observed test module covers transaction fetches, account transaction pagination, and stellar.toml caching. It does not exercise `fetch_account` with native + credit balances, trustline limit, authorization state, or malformed balance data.

That is the best seam for a transport-to-domain regression.

## Frontend contract and render seam

### TypeScript response type is still collapsed

`packages/ui/src/types/index.ts`  
Pinned blob: `796ce67c1db1e49faf0224146a20c979dcd3c131`

`AccountExplanation` has no `balances` member and there is no issue-specific `Balance` interface.

### Account UI renders summary cards inline

`packages/ui/src/components/AccountResult.tsx`  
Pinned blob: `cc6cd202de571cdd62e8b56e6fffa765a256a33f`

The account page currently renders:

- summary
- XLM balance card from `data.xlm_balance`
- “Other Assets” count from `data.asset_count`
- signers
- home domain
- flags

The issue text says to integrate an `AssetList` below `AccountBalances`, but current main has **no `AccountBalances.tsx`**. Those cards are inline in `AccountResult.tsx`.

The current `packages/ui/src/components/account/` directory contains only `TransactionHistoryTab.tsx`; `AssetList.tsx` does not exist.

Therefore the post-assignment UI change should integrate against the actual current component tree, not create an `AccountBalances` abstraction merely to match stale prose unless maintainers want that refactor for another reason.

### No account-asset UI regression exists on pinned tree

A recursive tree census of pinned main found no AccountResult/AssetList-specific UI test. The only observed `*.spec.tsx` files under the UI package are unrelated feature samples.

The UI package script surface is also lean:

`packages/ui/package.json` blob `4f73d504bd22fe6b34bb15fb1112813ce6f0decf`

- `npm run build` -> Next build
- `npm run lint` -> ESLint
- no package-level unit-test script observed

So acceptance verification needs to use the repository's actual supported frontend test strategy or add the smallest coherent test harness required by the issue, rather than claiming a test command that does not exist today.

## Recommended post-assignment data contract

The existing domain model and the requested API presentation have different needs. Avoid creating two indistinguishable `Balance` structs in adjacent layers.

A clean shape is:

1. Extend Horizon/domain `Balance` with optional/normalized trustline limit data.
2. Map domain balances to a presentation type such as `ExplainedBalance` / `AccountBalanceExplanation` owned by the explanation/HTTP contract.
3. Normalize native XLM explicitly:
   - `asset_code = "XLM"`
   - `asset_issuer = "native"`
   - `limit = "unlimited"`
4. For credit trust lines, require their code/issuer/limit to come from Horizon data; do not fabricate missing identifiers.
5. Preserve `is_authorized`.
6. Emit native XLM first deterministically, then retain Horizon order for the remaining lines unless maintainers prefer an explicit stable secondary sort.

The exact public type name is a maintainer/API-style choice; the important constraint is one source of truth for domain data and one explicit normalization boundary for presentation.

## Post-assignment implementation plan

1. Extend `HorizonBalance` to deserialize trustline `limit` safely.
2. Extend the existing domain balance model so the trustline limit survives `HorizonAccount::into_domain`.
3. Add transport-to-domain tests with:
   - native XLM,
   - multiple credit assets,
   - zero balance,
   - authorization true/false,
   - trustline limits.
4. Extend account explanation output with a normalized full balance list.
5. Test:
   - XLM first,
   - XLM code/issuer/limit normalization,
   - empty balance list,
   - only-XLM account,
   - zero-balance trust line,
   - authorization propagation,
   - multiple trust lines.
6. Extend `AccountExplanationResponse` and OpenAPI schema so the route actually serializes `balances[]`.
7. Update the account integration fixture and route/integration expectations.
8. Mirror the public balance shape in `packages/ui/src/types/index.ts`.
9. Add `packages/ui/src/components/account/AssetList.tsx` against the current tree:
   - XLM always visible,
   - non-native lines collapsed by default,
   - accurate “Show all N assets” count,
   - balance + limit display,
   - zero-balance muted styling,
   - issuer truncation + copy interaction,
   - empty and XLM-only behavior.
10. Integrate directly below the current inline balance/stat cards in `AccountResult.tsx`, unless a separate AccountBalances refactor is intentionally approved.
11. Use the repository's existing clipboard/toast conventions rather than a second notification system; verify current hooks/components before implementation.
12. Add focused frontend behavior tests or, if the package truly lacks an established renderer test harness, add a minimal supported harness with no unrelated migration.
13. Run Rust format/check/tests, UI lint, and the TypeScript/Next production build.

## Verification matrix after assignment

Backend:

- `cargo fmt --check`
- `cargo check -p stellar-explain-core`
- focused Horizon account-balance mapping test
- focused account explainer normalization tests
- route/integration fixture test
- full relevant core test suite

Frontend:

- `npm run lint -w packages/ui` or the repository-equivalent workspace command
- `npm run build -w packages/ui`
- focused AssetList/AccountResult tests using the supported test runner
- copy success/failure or reset behavior using mocked clipboard
- render cases:
  - empty `balances`
  - XLM only
  - XLM + one trust line
  - multiple trust lines collapsed/expanded
  - zero-balance trust line
  - long issuer truncation/copy
  - unauthorized trust line

No test requires a funded wallet or mainnet mutation. Use deterministic fixture data only.

## Application draft

> I checked current `main@5c1e18d1ed6e28734e8305a134c67613ed5d8e8c` before applying. The backend already retains Horizon balances in `models::account::Account`, so I would reuse that pipeline rather than add a second balance store. The real backend gap is two-stage: `HorizonBalance` currently drops the trustline `limit`, and `explain/account.rs` then collapses the retained balance list to only `xlm_balance + asset_count`; the route and integration fixture repeat that collapsed contract.
>
> On the frontend, the TypeScript AccountExplanation still has no balances array, `AssetList.tsx` does not exist, and the issue's referenced `AccountBalances` component is stale relative to main—the XLM/asset-count cards are inline in `AccountResult.tsx`. After assignment I would extend the existing Horizon/domain mapping with limit, expose a normalized presentation balance list (XLM first, issuer native, limit unlimited), update the route/OpenAPI/fixture, then add AssetList against the actual current component tree with collapse, zero-balance, issuer-copy, empty/XLM-only behavior and focused tests. I would run core fmt/check/tests plus UI lint and production TypeScript/Next build.
>
> I will wait for official assignment before assignment-dependent upstream changes.

No provider application success is claimed by this packet.

## Authority boundary

No upstream source, provider assignment, wallet, reward, payment, or funds are mutated. This owned-repository artifact preserves a SHA-pinned source/application packet for an authenticated provider seat to reuse.
