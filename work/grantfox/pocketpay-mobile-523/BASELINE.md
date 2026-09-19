# GrantFox baseline — Axionvera/pocketpay-mobile #523

Operation: `GFOX3-20260919-pocketpay-mobile-523/R-qr-receive-source-audit`  
Worker: ZZ-Sol-Crux-83 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `c3a24abacb45030eb4fef41aabc46b312aaf54a3`

## Canonical issue / provider fence

- GitHub: https://github.com/Axionvera/pocketpay-mobile/issues/523
- GrantFox: https://contribute.grantfox.xyz/org/Axionvera/repo/pocketpay-mobile/issue/523
- State observed: OPEN
- GitHub assignee: none
- GitHub public issue page: no development branch / PR shown
- GrantFox public assignment state: **Unassigned**
- Existing comments/applications: **0**
- GrantFox application route: visible; one application per user
- Labels: `hard`, `feature`, `ux`, `mobile`, `wallet`, `payments`, `Maybe Rewarded`, `GrantFox OSS`, `Official Campaign | FWC26`
- Connector upstream permission: `pull=true`, `push=false`

This packet is current-source evidence and an assignment-ready implementation contract. It does **not** apply, claim assignment, or mutate upstream source.

## Current source: the gap is real

`src/features/receive/qrPayload.ts` (`6556a1742e7c219f15c8b5e186056369857acdcb`) builds a bare Stellar destination or a `web+stellar:pay` request, but explicitly treats itself as pure formatting and does not validate destination or network. It also silently drops `assetCode` when `assetIssuer` is absent.

`app/receive.tsx` (`088b7a6aef30e9e7958ec22c8a9c07f533b1aac6`) validates optional amount and memo for the form, but on error passes the bad field as `undefined` to the builder. The QR therefore remains renderable and can encode a **different, valid-looking request** rather than refusing the invalid request.

The receive screen also trusts `walletStore.publicKey`, hardcodes the subtitle to Stellar Testnet, passes no configured network identity into payload validation, and does not bind QR validity to a supported/configured network.

Existing reusable primitives already exist in `src/utils/validation.ts` (`3401c0bb763b27fbb8f20028cee69da7bcdc989f`): `validateAddress`, `validateAmount`, and `validateMemo`. `src/features/settings/useNetworkEnvironment.ts` (`4bda5630ccb6a28c49efd0d5e69bc43c0b27f8c3`) classifies network tier as mainnet/testnet/custom, and `src/types/network.ts` already models `wrong-network`.

`__tests__/qrPayload.test.ts` (`18475a70e3f1d6779f9ca6312b4b4a8e9e384722`) covers formatting, including the current silent asset drop. `docs/qr-payment-requests.md` (`24b09221f8e5d2fb47abbb9c3eea4bb93d18af18`) documents the formatter-first contract. No focused test proves that invalid destination/network/amount/memo blocks QR generation and produces a clear invalid receive state.

## Narrow implementation contract after assignment

1. Add one `validateReceivePayload` (or equivalent) boundary next to `qrPayload.ts` that returns normalized valid parameters or structured field/environment errors.
2. Compose the existing address/amount/memo/network helpers; do not create a parallel validation stack.
3. Optional amount/memo remain optional, but when supplied must validate. Asset code + issuer are both present or neither; incomplete pairs are errors rather than silently dropped.
4. Do not invent an undocumented URI query parameter just to carry network identity. Validate the configured generation environment while keeping the emitted URI standards-compatible.
5. Make the Receive UI fail closed: invalid requests do not render/share a semantically different QR; show clear actionable errors; preserve Copy Address as the safe compatibility fallback.
6. Replace the unconditional Testnet assertion with configured environment copy.

## Required hostile/regression coverage

- valid bare-address request
- valid amount + memo request
- invalid destination
- zero / negative / non-numeric / >7-decimal amount
- memo over 28 UTF-8 bytes
- incomplete asset pair
- unsupported/wrong network environment
- blank optional fields equivalent to omitted fields
- invalid field cannot silently disappear into a different QR payload
- receive UI blocks QR/share while invalid
- correcting the field restores QR deterministically
- Copy Address remains available
- current valid formatting cases stay green

## Verification after assignment

- `npm test -- --runInBand`
- `npm run typecheck`
- `npm run lint`

Report exact exit codes and separate unrelated baseline failures from focused #523 evidence; do not weaken existing checks.

## Application draft

> Applying for #523 after auditing current `main@c3a24abacb45030eb4fef41aabc46b312aaf54a3`. The receive flow already has a good SEP-0007-style formatter and amount/memo validators, but the remaining safety gap is concrete: `receive.tsx` passes invalid amount/memo as `undefined`, so the QR can remain visible with those fields silently omitted; the builder trusts destination, has no network-validation seam, and silently drops an asset code without issuer. Existing tests cover formatting rather than “invalid request must not produce a different valid-looking QR.”
>
> After assignment I would add one structured receive-payload validation boundary that composes the existing address/amount/memo/network primitives, make invalid states fail closed for QR/share while preserving Copy Address as a fallback, replace the hardcoded network assertion with the configured environment, and add focused hostile + UI recovery tests. I’ll keep the URI standards-compatible and avoid a parallel validation stack.

## Authority / reward boundary

Campaign labels and `Maybe Rewarded` indicate campaign context only; no fixed reward, award, or payment is asserted. This packet does not apply to GrantFox, claim provider assignment, modify upstream code, or move funds.
