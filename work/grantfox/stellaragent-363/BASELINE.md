# GrantFox baseline — StellarAgent-AI-Agent-Payment-Rails/Stellar-agentic #363

Operation: `GFOX-STELLARAGENT-363-R-CINDER17-20260919`  
Worker: ZZ-Cinder-17 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `f92f0fcdfbfe4342184154e5cd4d3efd9168630f`

## Canonical issue

- GitHub: https://github.com/StellarAgent-AI-Agent-Payment-Rails/Stellar-agentic/issues/363
- GrantFox: https://contribute.grantfox.xyz/org/StellarAgent-AI-Agent-Payment-Rails/repo/Stellar-agentic/issue/363
- State at observation: OPEN / GitHub unassigned
- GrantFox state observed: Unassigned
- Existing GitHub issue comments observed: 2 prior assignment/application requests
- Matching #363 PR surfaced by fresh connector search: none
- Reward interpretation: campaign / Maybe Rewarded labels are eligibility signals only; no fixed award or payment is asserted here.

The provider work order requires official assignment before assignment-dependent implementation. This packet therefore records current-source truth and a concrete application plan only.

## Current-source trace

Historical PR notes saying channel/payment mutation paths are stubs are stale for current main.

At the pinned head:

- `packages/core/src/agent/mutations.ts` implements `open_channel` and payment mutation paths.
- `StellarAgent.create()` generates a random signer when neither `signer` nor `secretKey` is supplied. On testnet that generated account is Friendbot-funded via `fundFromFriendbot()`.
- `openChannel()` records the active channel; `getSpendReport()` and `getChannel()` expose current channel state.
- `contracts/payment_channel/src/lib.rs` rejects `spent_this_period + amount > limit_per_period` with `spend limit exceeded for this period`.
- `packages/core/src/agent/invocation.ts` normalizes that contract text to stable SDK code `SPEND_LIMIT_EXCEEDED`, with invocation/local integration coverage.
- The CLI currently exposes route preview; it does not provide create/open/pay commands. A truthful end-to-end tutorial should use the TypeScript SDK instead of inventing CLI verbs.

## Tutorial-critical source gaps

1. **Install name drift.** The README Quick Start says `npm install @stellaragent/sdk`, while `packages/core/package.json` declares the actual TypeScript core package as `@stellaragent/core@0.1.0`. The tutorial must verify the install surface instead of copying the README line blindly.
2. **No checked-in ready testnet deployment.** `deployments/` contains `example.json`, not a usable `testnet.json`.
3. **Deployment has real prerequisites.** `docs/deployment.md` requires Rust + Stellar CLI + pnpm, a funded testnet identity, and deployment/wiring of seven contracts. Circuit-breaker setup requires at least five actual trusted-node addresses because quorum is five.
4. **Contract addresses fail closed.** Normal `StellarAgent.create()` validates deployed contract IDs. `allowUnconfiguredContracts: true` is for contract-free/read-only cases and does not turn contract mutations into a runnable tutorial.

## Pinned evidence

- upstream main: `f92f0fcdfbfe4342184154e5cd4d3efd9168630f`
- `packages/core/package.json`: `db6101002878b09b61e4f581e4fe38de867be38a`
- `packages/core/src/agent/StellarAgent.ts`: `d9405cbab09482c0d012f522f5cc6d49b672f38c`
- `packages/core/src/agent/invocation.ts`: `88a7e88478462b182aa877eb5e336fb446131072`
- `packages/core/src/agent/queries.ts`: `242e63881b6ae29d9da51aeaee680ce8e5bed2f4`
- `packages/core/src/errors.ts`: `3b764e801980d17ecb9bd814dfe74216b0cd78a2`
- `packages/cli/src/index.ts`: `8c1c9b16b60e24a6dfb0a31b58a0a45b4aa80eee`
- `integrations/examples/pay-for-api.ts`: `5664b2c1a6a16f0341a72d617e211038321d2401`
- `deployments/example.json`: `3200f8b201d50507f1e37626468491c14ab9965e`

## Assignment-gated implementation plan

After provider/maintainer assignment:

1. Add one copy-pasteable document starting from a clean directory and the verified package/install command.
2. Make testnet setup explicit: tool versions, generated/funded account, seven-contract deployment, five trusted nodes, emitted deployment/env file, and the exact environment variables consumed by the SDK.
3. Provide one TypeScript program that creates/restores the agent, opens an XLM channel, records the channel id/address, pays a test recipient, and prints transaction hash, ledger, spend report, and channel state.
4. Deliberately attempt an over-limit payment and show the stable `SPEND_LIMIT_EXCEEDED` error without claiming the rejected call moved funds.
5. Run the tutorial against testnet using only testnet funds; capture exact commands, relevant output, tx hash/ledger, and the rejected limit attempt.
6. Link the tutorial as the README's first onboarding CTA and keep deployment/security caveats intact.

## Provider application draft

> Applying for #363 from current `main@f92f0fcdfbfe4342184154e5cd4d3efd9168630f`. I traced the live TypeScript path end to end before proposing docs: current main has real `openChannel()` / `payForAPI()` Soroban mutations; a newly generated testnet agent is Friendbot-funded; `getSpendReport()` / `getChannel()` expose state; and payment-channel limit panics normalize to `SPEND_LIMIT_EXCEEDED`. I also found two tutorial-breaking seams a generic draft could miss: README says `@stellaragent/sdk` while the actual core workspace package is `@stellaragent/core`, and the repository has only `deployments/example.json`, so a real tutorial must walk through seven-contract testnet deployment and the five-node circuit-breaker quorum rather than assuming ready addresses.
>
> After assignment I will add one clean-directory, copy-pasteable testnet tutorial using the TypeScript SDK; record a real successful transaction hash/ledger and spend/channel output; deliberately demonstrate the over-limit failure; and make that tutorial the README's first CTA while preserving existing deployment/security guidance. Only testnet funds will be used.

## Publication / authority notes

The linked GitHub integration has read access but no upstream push permission. That is not a statement about the human account's broader permissions. No upstream source, issue state, provider assignment, wallet, funded payout address, payment, or reward state is mutated by this baseline.
