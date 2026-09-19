# GrantFox baseline — Stellar-VaultLink/invofi #184

Operation: `ZZA-GFOX-INVOFI-184-R-ZZ-SOL-FIREBREAK`  
Worker: ZZ-Sol-Firebreak · GPT-5.6 Sol  
Observed: 2026-09-19  
Application repository default branch: `main`  
Pinned application head: `afc37076869e590fdc660e68df6f767fbfdbf1f6`  
Contract repository default branch: `master`  
Pinned contract-repository head: `cebe97a83325da025a9d5ce212a56f8d2fbfcca7`

## Canonical issue and provider state

- GitHub: https://github.com/Stellar-VaultLink/invofi/issues/184
- GrantFox: https://contribute.grantfox.xyz/org/Stellar-VaultLink/repo/invofi/issue/184
- GitHub state: OPEN
- GitHub assignee: none
- Issue comments observed: 2 existing applications
- Matching open PR search for issue number 184: none surfaced
- GrantFox public state at live fetch: **Unassigned**, **Apply to this issue**
- Provider route: one application per user, direct GitHub comment
- Labels observed: `medium`, `infra`, `ci`, `GrantFox OSS`
- Upstream connector permission: pull=true, push=false

This packet is pre-assignment research plus an application-ready implementation contract. It does not mutate upstream source.

## Repository boundary

ADR-0007 makes the ownership split explicit:

- `Stellar-VaultLink/invofi` owns the frontend, `apps/sdk`, application scripts, CI and application documentation.
- `Stellar-VaultLink/invofi-contracts` owns Soroban contract source, contract tests and deployments.

That matters for #184: the drift gate belongs in the application repository, but its refresh step must consume contract/deployment interface authority without turning routine PR CI into a cross-repository live-network dependency.

Pinned boundary blob:

- `docs/adr/0007-repo-topology-and-sdk.md`: `cd7752cb3c77a29d34e37e0c5895f958ac364e6d`

## Current ABI authority and the real gap

The SDK already has an explicit method-table layer:

- `invofi/apps/sdk/src/types/contract-abi.ts`: `ddd06c1284a6635876ef2a78a494f9b86fbb582d`
- `invofi/apps/sdk/src/contracts/builder.ts`: `dbbd59fc0d6a96b6d4b53f3b9a0ddc2234be7bd8`
- registry adapter: `be5d135bc7fbdea68aa48647f9dc63eb766dca46`
- financing adapter: `3a9e7a360a1c67858ceeef1f2e2a3b444af11a14`
- repayment adapter: `95acf5c63ab3477d06f1a257ba3ec9d8cc3d8abc`
- position-token adapter: `579982e60c376d4b365c29d0142361fa6f38127b`

`contract-abi.ts` calls itself the single source of truth for the typed builder and documents regeneration from `stellar contract inspect ... --output json`. It currently exposes 14 typed entries across four namespaces:

- registry: `register_invoice`, `get_invoice`, `cancel_invoice`
- financing: `create_offer`, `get_offer`, `accept_offer`, `reject_offer`, `get_position_token`
- repayment: `repay_invoice`, `mark_overdue`, `reclaim_invoice`
- position token: `balance`, `decimals`, `transfer`

The gap is therefore not “create an ABI abstraction.” The gap is **prove the committed SDK-facing ABI stays compatible with a pinned deployed-contract spec generation**.

## Existing CI does not close #184

Current `.github/workflows/ci.yml` blob `7f6c287ee5e02cc73c68bb8fceb99e8343277782` runs:

- frontend lint
- frontend TypeScript checking
- `scripts/check-sdk-parity.js`
- unit/build/bundle/smoke/a11y/wallet jobs
- commitlint

But `scripts/check-sdk-parity.js` blob `00ee7be2655ffc4d47c968b330bb93da648430fb` only scans frontend source for raw `contract.invoke`, `createInvofiClient`, or `new Contract` use outside the approved boundary. It does **not** inspect deployed contract specs or compare on-chain method/parameter shapes with `contract-abi.ts`.

The CI file already contains the three fixed deployed testnet contract IDs for registry, financing and repayment, which gives a natural explicit refresh target without inventing runtime negotiation.

## Critical normalization seam

A correct comparator must not naïvely compare every SDK wrapper parameter 1:1 against Soroban function parameters.

Current typed wrappers intentionally contain invocation/transport inputs:

- `source_account` on read wrappers is optional SDK source-account context.
- `token_id` on `POSITION_TOKEN_ABI` selects the dynamic SEP-41 contract instance; the file itself says the token ID is a regular SDK parameter because the contract ID is dynamic.

Those values are required by the client invocation layer but are not necessarily arguments in the deployed Soroban function spec. A raw object-shape diff would therefore create false drift failures.

The post-assignment implementation should define one explicit normalization contract: compare **on-chain method name + actual on-chain argument names/types**, while separately retaining/validating SDK-only routing metadata. Do not “fix” this by silently deleting useful SDK parameters or by teaching CI to ignore arbitrary mismatches.

## Bounded implementation contract after assignment

1. **Committed normalized snapshot**
   - Store a deterministic JSON snapshot of the deployed registry, financing, repayment and intended position-token function specs.
   - Include provenance: network, contract ID (or dynamic token source identity), refresh timestamp, Stellar CLI/RPC version/source, and normalized method signatures.
   - Canonically sort methods and parameters so refreshes are reviewable and byte-stable.

2. **Hermetic comparator**
   - Routine PR CI reads only repository bytes: committed snapshot + SDK ABI metadata.
   - Fail on an SDK-covered on-chain method disappearing/renaming.
   - Fail on required on-chain parameter addition/removal, name drift, ordering drift when order is semantically relevant, or incompatible type drift.
   - Report newly deployed methods not yet surfaced by the SDK as informational, exit 0.
   - Treat SDK-only invocation metadata (`source_account`, dynamic `token_id`) through an explicit allowlisted/typed normalization rule, not a blanket ignore.

3. **Explicit networked refresh**
   - Provide a deliberate operator command that uses Stellar CLI/RPC against the configured deployed IDs and rewrites the canonical snapshot.
   - Normal PR CI must never require RPC availability.
   - A failed/partial refresh must not overwrite the last complete snapshot.

4. **Hostile regression matrix**
   - simulated rename of an SDK-covered method => non-zero
   - required parameter added => non-zero
   - parameter removed => non-zero
   - incompatible type changed => non-zero
   - on-chain additive method => informational + zero
   - reordered/noncanonical snapshot input => deterministic normalization or explicit refusal
   - transport-only SDK metadata => no false positive
   - missing one contract snapshot / malformed spec => fail closed

5. **CI integration and docs**
   - Wire the hermetic check into existing CI/package conventions.
   - Document the refresh command and exactly which deployed IDs/network it reads.
   - Keep runtime contract-version negotiation out of scope (#96).

## Verification plan

After assignment, report exact commands and exit codes for at least:

- SDK `npm run type-check`
- SDK `npm test`
- the new ABI-drift unit/fixture suite
- the hermetic comparator against the committed canonical snapshot
- the simulated-rename negative witness
- the additive-method informational witness
- the repository CI-equivalent command(s) affected by the change

No claim of deployed-spec correctness should be made without recording the exact refresh provenance.

## Application text used for provider route

The provider application is source-specific: it identifies the current ABI blob, the existing parity checker’s actual limits, the current CI blob, the committed-snapshot/hermetic-CI boundary, hostile rename/parameter tests, and the explicit exclusion of runtime version negotiation.

One additional implementation note discovered after the initial application draft: the comparator needs the normalization seam above for `source_account` and dynamic `token_id` so it does not confuse invocation metadata with Soroban arguments.

## Authority boundary

The upstream repositories are pull-only through this connector. No source implementation begins before GrantFox/maintainer assignment if the provider requires assignment.

No upstream code, issue state, provider assignment, deployment, wallet, reward, payment or runtime network state is mutated by this baseline packet.
