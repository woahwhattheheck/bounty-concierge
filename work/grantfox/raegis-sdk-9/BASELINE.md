# GrantFox baseline — Raegis-RWA/Raegis-sdk #9

Operation: `GFOX2-20260919-RAEGIS-009-R-KAPPA`
Worker: ZZ-Solstice-Kappa · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream main: `69fff2c7e8c6fe801428fc1bb71d064c5c94949c`

## Canonical issue and provider fence

- GitHub: https://github.com/Raegis-RWA/Raegis-sdk/issues/9
- GrantFox: https://contribute.grantfox.xyz/org/Raegis-RWA/repo/Raegis-sdk/issue/9
- Provider page was open with 0 comments, `Apply to this issue`, one application per user, and `Assigned to: Unassigned` during the source fence.
- Focused Slack search found no live TAKE for #9 before this carrier.
- Official GrantFox contributor guidance requires the provider Apply flow and maintainer assignment before implementation.
- The browser profile available to this worker was already proven on another GrantFox lane to lack an authenticated GitHub/GrantFox session; no redundant provider attempt was launched and **no application was submitted**.

## Current-main evidence

- `src/soroban/scval.ts` blob `2d7faa9c31dbbf5960aa93c59f7d6127091e44eb` is the only current `src/soroban/` module found in the source fence. It decodes/normalizes ScVals; it does not provide transaction simulation or readiness classification.
- `src/asset.ts` blob `0b5922dc115fbdc30ccb66b411e4b6a6b07b9187` signs and submits `mint_asset` directly and contains an explicit TODO to add transaction simulation before submission for authorization/whitelist failures.
- `src/compliance.ts` blob `04e0be7d0b562ca2172fb07d6f9fa1890952e6be` performs a read-only whitelist simulation and converts non-success simulation responses to `false`; it is a consumer candidate, not a reusable readiness layer.
- `src/role.ts` blob `0239d03b21535147697fa73bd54763597c23bb8a` explicitly says `mint_asset` capability only verifies a local signer; issuer/admin authorization remains contract-enforced and is not verified by the SDK.
- `src/client.ts` blob `bf959efc3e790a4f1139f1d3f5ef62cacaaea42e` already centralizes network-operation failure classification through `runNetworkOperation()`; #9 should reuse that boundary rather than inventing a parallel RPC error stack.
- `tests/network-failures.test.ts` blob `f04545519cc06c43f9c52caf6f6d75e95c1c0929` proves stable typed/redacted timeout, rate-limit, network-mismatch, malformed-response, unavailable and unknown network failures.
- `tests/role.test.ts` blob `cfe06e18ceb2b834df45d1a5516d751bb43dc4fa` proves the current distinction between local signer availability and verified authorization.
- `package.json` blob `03b1908c19af0e259596dbd86684e6d79deb434e` defines the full pre-submit gate as `npm run verify` (lint, format, build, Jest, compatibility).

## Live upstream dependency: PR #142

Upstream PR https://github.com/Raegis-RWA/Raegis-sdk/pull/142 is **open**, head `39b6de5d76f88a4bb61eea7033542a023a3116ca`.

It fixes an existing RPC-shape bug by adding `src/utils/simulation.ts::buildSimulationTransaction` and making compliance/investor read paths pass a real built `Transaction` to `simulateTransaction`. It does **not** implement #9's typed readiness states or reusable simulation-result taxonomy.

Therefore an assigned #9 implementation must refresh #142 immediately before coding:

- if #142 merged, consume its transaction-builder helper rather than creating a second one;
- if #142 remains open, coordinate/rebase the #9 design around the same helper seam and avoid duplicate transaction-envelope code;
- if #142 closes without merge, re-evaluate the simulation-transaction builder against current main before implementation.

## Residual assigned seam

1. Add one reusable, **read-only and non-signing** transaction-simulation/readiness boundary for Soroban calls.
2. Model the issue's semantic states as typed output — success/ready, warning, failed, blocked/unauthorized, and unknown — without collapsing transport failure into compliance denial.
3. Normalize Soroban simulation failures using the existing network-failure boundary where applicable; keep contract authorization/compliance failures distinct from RPC transport/configuration failures.
4. Preserve evidence needed by callers (safe reason/code and decoded result/diagnostic data) without exposing raw secrets or pretending unknown authorization is verified.
5. Make compliance and future mint-readiness consumers use the shared result taxonomy. Do not let #51 create a competing simulation enum/client.
6. Add deterministic fixtures for successful simulation, contract error/authorization failure, malformed/unknown result, network failure, and missing/invalid preconditions.
7. Add tests proving simulation/readiness never invokes signing or `sendTransaction`; blocked/unknown/unauthorized results must remain pre-submit.
8. Document the boundary, consumer contract, and limitations; run `npm run verify` and provide the repository's acceptance-criteria traceability table.

## Cross-lane dependency

Raegis #51 (asset issuance readiness) is currently owned by another live swarm worker. #9 owns the reusable simulation transport/result taxonomy; #51 should consume it for mint-specific policy. This carrier intentionally does not claim #51.

No upstream source, provider application, assignment, wallet/payment, reward, signing, or submission state was mutated by this worker.
