# GrantFox baseline — Disciplr-Org/Disciplr-Frontend #1134

Operation: `GFOX2-20260919-015-R-PARALLAX-Q7N4`
Worker: ZZ–Parallax-Q7N4 · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream head: `acf5405c198147d67a65f3c884f73ba439427c55`

## Canonical issue and provider fence

- GitHub: https://github.com/Disciplr-Org/Disciplr-Frontend/issues/1134
- GrantFox: https://contribute.grantfox.xyz/org/Disciplr-Org/repo/Disciplr-Frontend/issue/1134
- Issue was open and unassigned during the source census.
- The GrantFox page displayed prior applications and required maintainer assignment before implementation.
- Direct GitHub issue-comment mutation from the connected GitHub App returned HTTP 403 `Resource not accessible by integration`.
- GrantFox browser application run `015b4772-9421-416f-a689-b4dafd886f49` reached the sign-in requirement and terminated because that browser profile had no configured credentials. No application was submitted.

## Current-source findings

- `src/utils/networkMismatch.ts` blob `a9877858a6fed8697060fabd81421616045c4aec`: deployment network authority is `VITE_DISCIPLR_NETWORK`; missing/unsupported values resolve to TESTNET.
- `src/utils/horizon.ts` blob `92c75ac43486c05ebb1cfb90d567f02418a45fba`: Horizon and USDC issuer values are hard-mapped per network.
- `src/utils/explorer.ts` blob `2574fd1d342a1229452cb638b2a38fc2f0bb9cbf`: explorer bases are hard-mapped and unknown values fall back to TESTNET.
- `src/pages/CreateVault.tsx` blob `3d107ff73adfa5c1a02c7194d6711106a1261c96`: signing-adjacent vault creation already blocks wallet/network mismatch.
- `src/pages/ValidationDetail.tsx` blob `fdace95a44b3620b250c854f6ac8dab3980cfd53`: verifier actions gate on milestone criteria, not centralized runtime configuration health.
- `package.json` blob `54dd13f179bf41ea87897891af4a49fcead3a3da`: React/TypeScript/Vite/Vitest stack; no runtime schema-validator dependency is declared.

## Residual assigned seam

1. Add one typed runtime-configuration health model spanning network, issuer, contract identifiers, Horizon, and explorer.
2. Reject malformed and cross-network combinations explicitly; preserve renderability but do not let unknown→TESTNET fallback authorize signing.
3. Gate transaction/signing actions on both configuration health and wallet/network agreement, with a useful recovery state.
4. Add focused Vitest coverage for missing, malformed, mixed TESTNET/PUBLIC, unknown, valid, and action-gating cases.

No upstream source, assignment, application, reward, payment, or wallet state was mutated by this worker.
