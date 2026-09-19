# GrantFox source baseline — Raegis-RWA/Raegis-sdk #51

Operation: `GFOX3-20260919-RAEGIS51/R-mint-readiness-baseline`  
Worker: ZZ-Solstice · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `69fff2c7e8c6fe801428fc1bb71d064c5c94949c`

## Canonical issue and authority

- GitHub issue: https://github.com/Raegis-RWA/Raegis-sdk/issues/51
- Title: **Implement SDK asset issuance readiness checks**
- GitHub state at the source fence: open, zero comments, no assignee.
- Campaign labels at the source fence: `GrantFox OSS`, `Maybe Rewarded`, `Official Campaign | FWC26` plus SDK/feature/expert/RWA/minting labels.
- Targeted open-PR search for issue 51 returned no matching open carrier.
- Connector repository metadata is read-only for this upstream installation (`pull=true`, `push=false`). A collaborator-permission probe returned the provider error `403 Resource not accessible by integration`; that is recorded as connector/install access evidence, not as a claim about the human account's permissions.
- This packet is **pre-assignment source evidence only**. It does not apply for the bounty, assign work, mutate upstream source, authorize signing/submission, or establish a reward/award/payment.

## Current-main mint path: unsafe preflight gap is real

Pinned source: `src/asset.ts@0b5922dc115fbdc30ccb66b411e4b6a6b07b9187`.

`AssetModule.mint(to, amount)` currently:

1. calls `requireSigner()`;
2. builds `mint_asset` with signer, recipient, and amount;
3. contains an explicit TODO to add transaction simulation before submit for auth/whitelist failures;
4. constructs a transaction with a placeholder sequence number;
5. calls `tx.sign(signer)`;
6. calls `rpcServer.sendTransaction(tx)`.

There is no composed readiness result between input and signing. The issue's requested "blocks unsafe minting before transaction signing" behavior is therefore not satisfied by current main.

The migration example `examples/migration/mint-transfer-before-after.ts@9a9f1fa1534346443cd72b269ee7bed513a308f2` mirrors this direct-sign/direct-submit flow, so the eventual docs/example change has a concrete home.

## Reusable primitives already present

### Recipient compliance

Pinned source: `src/compliance.ts@04e0be7d0b562ca2172fb07d6f9fa1890952e6be`.

`ComplianceModule.checkWhitelist(address)` calls the contract's read-only `is_whitelisted` function through simulation and returns a boolean. A readiness implementation should compose this primitive instead of duplicating whitelist logic.

### Signer/capability context, with an explicit authority limitation

Pinned source: `src/role.ts@0239d03b21535147697fa73bd54763597c23bb8a`.  
Pinned docs: `docs/role-discovery.md@ce52da34b09eb8dc0387a8e9cef046b6b54dc112`.  
Pinned tests: `tests/role.test.ts@cfe06e18ceb2b834df45d1a5516d751bb43dc4fa`.

`RoleModule.checkCapability(address, 'mint_asset')` verifies only that a matching local signer exists. It deliberately returns `verified: false` and states that issuer/admin authorization is contract-enforced and **not verifiable by the current SDK**. The docs likewise state that the contract exposes no `get_role` / `is_admin`-style query.

A readiness feature must preserve that truth. It must not relabel local-signer presence as issuer authorization. If the deployed contract still exposes no issuer/admin read at implementation time, the readiness result needs an explicit `unverified`/blocked-or-warning state rather than a false verified pass.

### Network failure boundary

Pinned sources:

- `src/client.ts@bf959efc3e790a4f1139f1d3f5ef62cacaaea42e`
- `src/network/failures.ts@872a5409d2f0c87657a6998a1303c659a0ca7380`
- `src/diagnostics/network.ts@a2955a554cb6fee7d2b7f6b5d3fdc730130edc7a`
- `tests/network-failures.test.ts@f04545519cc06c43f9c52caf6f6d75e95c1c0929`

`AegisClient.runNetworkOperation()` already maps RPC failures into typed `NetworkFailure` codes (timeout, RPC unavailable, rate limited, bad passphrase, malformed response, unknown). Readiness should reuse this boundary for network checks and surface a stable blocked reason rather than parsing provider-specific strings.

### Test fixtures / public surface

Pinned sources:

- `src/testing/fixtures.ts@08ca6680fc1eda2ff9663cb8d8637e0cee0b75ab`
- `src/testing/mock-client.ts@0eed26507070cbf74a13889c80421e02645e6122`
- `src/index.ts@b498dcfb2008865b3a98ad0ceaa330ff7a89a547`
- `docs/testing.md@c92da79c888067dd37d3e86677e346b352672e1e`

The repo already has deterministic fake transaction hashes, ephemeral test keypairs, whitelist state, a mock asset module, and a dedicated `@aegis/sdk/testing` subpath. The readiness work should extend these rather than invent a second fixture framework. If readiness becomes public SDK API, production exports belong on the main entrypoint; test-only builders remain under the testing subpath.

## Assignment-ready implementation map

Only after provider/maintainer assignment:

1. **Add a typed model** such as `MintReadinessResult` plus stable blocked-reason codes. Keep "verified" separate per check so an unknown issuer/asset fact cannot be mistaken for a pass.
2. **Validate input before network/signing**: recipient address shape, finite positive integer/base-unit amount (consistent with the current `i128` call contract), signer presence/match, and client/network configuration.
3. **Compose recipient compliance** using `ComplianceModule.checkWhitelist(to)`.
4. **Compose network readiness** through the existing `runNetworkOperation` / `NetworkFailure` boundary. Do not introduce a parallel raw-error taxonomy.
5. **Issuer/admin authorization**: use a real contract read only if the pinned deployed contract/API exposes one at implementation time. Otherwise return an explicit unverified/blocked-or-warning result and document the limitation. Local `mint_asset` capability is not sufficient evidence.
6. **Asset status**: likewise use an actual contract-exposed active/paused/status read if it exists. If none exists, preserve an explicit unknown/unverified state rather than manufacturing readiness.
7. **Expose a read-only preflight** such as `asset.checkMintReadiness(to, amount)` (exact naming should follow maintainer preference). It must not sign or submit.
8. **Gate `mint()` before signing** on the readiness result, with a compatibility decision documented. A blocked result must be observable before transaction construction/signing/submission.
9. **Tests must prove the safety boundary**: blocked recipient, missing signer, invalid amount/address, compliance RPC failure, network/config failure, and any issuer/asset unknown/denied state must not call `tx.sign()` or `rpcServer.sendTransaction()`. Include a happy path and fixture/mock coverage.
10. **Docs/example**: update the mint example to review readiness before signing and explain which checks are contract-verified versus SDK-local/unverified.
11. **Run the repo's full pre-submit gate**: `npm run verify`, which is pinned in `package.json@03b1908c19af0e259596dbd86684e6d79deb434e` and `docs/verification.md@c8fcafeb41cc1efd4ab52ac5b719f3a4fe5ff4b1`.

## Acceptance traceability

| Issue acceptance | Current source | Post-assignment target |
| --- | --- | --- |
| Mint readiness model | absent | typed result + stable reason codes |
| Issuer check | local signer only; issuer/admin not verifiable | real read if available, else explicit unverified state |
| Recipient check | `checkWhitelist` exists | compose it |
| Asset status | no SDK status read surfaced in pinned source tree | real read if available, else explicit unknown/unverified |
| Amount check | no readiness validation before `nativeToScVal(... i128)` | validate before construction/signing |
| Network check | typed network failure layer exists | compose it into readiness |
| Blocked states typed | network/role types exist separately | one mint-readiness blocked-reason union |
| Compliant/non-compliant tests | role/network tests exist, no mint-readiness suite | add safety-boundary tests |
| Docs limitations | role docs already explain issuer limitation | readiness docs must preserve it |
| Review before signing example | current example signs/submits directly | readiness-first example |

## Verification / scope fence

This is static source analysis at the pinned upstream commit. No live contract exploit, transaction simulation against a real asset, signature, submission, GrantFox application, assignment, award, wallet, or payment action was performed.

"Maybe Rewarded" remains discretionary campaign metadata, not proof of a fixed reward or payment.
