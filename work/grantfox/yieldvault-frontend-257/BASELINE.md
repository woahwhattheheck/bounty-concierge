# GrantFox baseline — YieldVault-Org/YieldVault-Frontend #257

Operation: `GFOX3-20260919-yieldvault-frontend-257-R-ZZ-SOL-DELTA`  
Worker: ZZ-Sol-Delta · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `f0ce8b799ded8f8e4cc158069d1940b1f822eb17`

## Canonical issue/provider state

- GitHub: https://github.com/YieldVault-Org/YieldVault-Frontend/issues/257
- GrantFox: https://contribute.grantfox.xyz/org/YieldVault-Org/repo/YieldVault-Frontend/issue/257
- Issue: OPEN; assignee: none; one existing generic application comment
- GrantFox public state: Unassigned; `Apply to this issue` visible; one application per user
- Exact issue-linked PR search returned no carrier
- Connector permission snapshot: pull=true, push=false
- Labels include `enhancement`, `GRANTFOX OSS`, `MAYBE REWARDED`, `priority:medium`, `Third Campaign`
- Reward interpretation: possible/discretionary only; no fixed award, assignment, or payment is asserted.

This is a pre-assignment amount-boundary packet. It does not mutate upstream source, submit a transaction, or use funded/mainnet assets.

## Why the current “numeric parsing” fix does not close #257

The issue was opened after commit `70fe9c94477ae0c0754b36f4ab9fbfd1d681c6ff` (“fix: stabilize frontend tests and numeric parsing”).

That commit changed the shared parser to strip commas before `Number()`. It did **not** introduce an explicit locale grammar, canonical decimal representation, asset precision policy, or non-binary serialization boundary. Current main therefore still has the exact class of correctness risk #257 describes.

## Input boundary: AmountInput is en-US-specific and punctuation-destructive

`src/components/AmountInput.tsx`  
Pinned blob: `b9e8e006923ae8408df78d716339e34576d0b501`

Current behavior:

- local parser removes all commas and calls `parseFloat`
- display is hard-coded to `toLocaleString('en-US', ...)`
- input sanitizer removes everything except digits, dot, and minus
- repeated dots are collapsed into one decimal separator
- `min` and `step` are passed to an input whose type is `text`, so they are not a numeric serialization contract
- values above `Number.MAX_SAFE_INTEGER` are rejected, but fractional precision is not protected

Concrete locale failure: an input such as `1,23`, when interpreted as decimal-comma notation, is sanitized to `123`. The current code cannot distinguish a decimal comma from a thousands separator because it has no locale grammar.

The existing AmountInput test blob `ebdc1b3a26844e60fa46e897efb8670f34b52b61` covers en-US comma grouping and safe-integer magnitude, but not locale matrices, malformed grouping, fractional precision, canonical round trips, or per-asset minimum units.

## Shared parse/display helpers still convert through binary Number

`src/utils/format.js`  
Pinned blob: `372d6f8e3e016776eb041acd5f136f7a1793f525`

`safeParseNumber`:

1. strips commas from string input,
2. calls `Number(normalizedValue)`,
3. computes a string form of the resulting number,
4. never compares that reconstructed value to the original decimal,
5. only rejects large values when string length and safe-integer magnitude cross a coarse threshold.

That means a long fractional decimal can be rounded by IEEE-754 while remaining far below `Number.MAX_SAFE_INTEGER`.

`formatAmount` separately calls `Number(value)` and formats a fixed number of decimal places, default 2. This is acceptable as a presentation helper only if it is fed from a preserved canonical amount and never reused to determine the amount to submit.

Pinned format test blob `e40849b721d6620f881735bdc3879ce7ab8a7d08` tests en-US grouping and safe-integer limits, not decimal identity or locale round trips.

## Validation is downstream of the lossy parser

`src/utils/validate.js`  
Pinned blob: `849c864013419820d60c3cf25d57e98068a1a5fb`

Deposit/withdraw validation calls `safeParseNumber`, then compares the resulting binary number against numeric balance/position values.

It has useful empty/positive/insufficient-balance errors, but no explicit:

- decimal precision limit
- minimum unit
- rounding policy
- locale grammar
- canonical-string validation
- asset-specific decimals

Pinned validation tests `a4020a2d5c9440bd2d08cd74ce22d2499bcb8a0e` therefore prove the current Number-based behavior, not #257's deterministic decimal boundary.

## The transaction-facing form path explicitly calls Number(amount)

`src/components/DepositForm.tsx`  
Blob: `e52209803f083e95d3ea38394bdd644f2024b39a`

After string validation, submit calls:

`vaultService.deposit(vault.id, Number(amount))`

`src/components/WithdrawForm.tsx`  
Blob: `807745121848993fc5f253cb76d2e79b39ad8b3b`

It performs the same conversion before withdrawal submission.

Both forms preview shares using the existing Number-backed share helpers.

This is the central boundary to change after assignment: a valid human-entered decimal must not be converted to binary floating point before the service/transaction layer decides its exact integer/minor-unit representation.

## Wizard flows bypass AmountInput entirely

`src/components/DepositWizard.jsx`  
Blob: `ea4344e711caf8cc4a1e7e232dd747c4cdb52602`

`src/components/WithdrawWizard.jsx`  
Blob: `f1525ebcb3ec85c905567fe437317efa2fd71180`

Both wizard amount steps use native `<input type="number">`, not `AmountInput`.

Both submit through `Number(data.amount)`.

The withdrawal review also computes remaining position with `deposited - Number(data.amount)`.

Therefore an implementation that fixes only `AmountInput` would leave the multi-step transaction flows on a different parser and the same binary-number submission boundary.

## Share previews also use binary arithmetic

`src/utils/shares.js`  
Blob: `b5f0dfe0ce4e352d402b4781a08a9255a97f995f`

`sharePrice`, `previewDeposit`, `previewWithdraw`, and `previewRedeem` ultimately operate on JavaScript numbers and division/multiplication.

Pinned tests `28270bebfbbce5fc7101e9a50e4ece909a0f1ca7` cover simple arithmetic and safe-integer rejection, not deterministic decimal/minor-unit behavior.

A correct fix does not necessarily require rewriting every analytical/statistical number in the UI, but the amount shown as the transaction preview must reconcile with the exact canonical amount used at submission.

## Locale configuration is currently only en-US

`src/constants/i18n.js`  
Blob: `834f3bb05b65d334ca33a51127a5f0b003a228cb`

Current supported language/locale state:

- `SUPPORTED_LANGS = ['en']`
- `DEFAULT_LOCALE = 'en-US'`

So current production “supported locales” is effectively one formatting locale. #257 should still establish an explicit locale parser contract and a test matrix so adding another supported locale cannot silently change transaction meaning.

The implementation should not claim `de-DE` or `fr-FR` are production-supported unless that support is intentionally added. They are valuable hostile/property fixtures for proving parser rules, but product locale scope should stay explicit.

## No asset precision metadata exists in the observed vault fixtures

`src/services/mockData.js`  
Blob: `8c61ddb294eee4b84495f9b39605c29040967179`

Vaults identify asset codes (USDC/XLM/EURC) but carry no decimal/minimum-unit metadata. Balances, total assets, shares, positions, APY, and TVL are JavaScript numbers.

The issue requires “supported precision, minimum units, rounding” to be visible before signing. Those values cannot safely be guessed from the asset symbol alone. The assigned implementation should introduce an explicit precision source/config boundary or consume one from the actual token/contract metadata when that integration exists.

## Current service/wallet layer is mock-only

`src/services/vault.js`  
Blob: `aaacb749ee2533caa52aec984dec60b758d60622`

Deposit/withdraw accept a numeric `amount` and return locally computed mock shares.

`src/services/wallet.js`  
Blob: `e8aa9d5f64493082f058d2efd1138f1c8f6e8bb8`

The wallet is explicitly mocked:

- balances come from mock data
- no network call is made
- `signAndSubmit` accepts only a human-readable summary and returns a mock hash

No real contract amount serializer exists on current main. The correct scope is therefore to establish the exact canonical boundary now so a future live wallet/contract adapter cannot accidentally inherit Number-based amounts. Do not invent a fake “on-chain serializer” just to satisfy the issue text.

Recursive/source search also surfaced no dedicated fee implementation on the pinned tree. Fee display/serialization should use the same separation once a real fee value exists, but #257 should not manufacture unrelated fee transport code.

## Existing CI

`.github/workflows/ci.yml`  
Blob: `e0de7e895eadefcae74e0601edbd35341cb1f1bd`

CI runs:

- Node 22
- `npm install --no-audit --no-fund`
- `npm test`
- `npm run build`

Root package blob `c30544e7574b0476e6da97928b1c23118fb85d32` uses Vitest and TypeScript/Vite, so property/boundary tests can live in the existing harness.

## Recommended post-assignment amount contract

Create a dedicated amount boundary with three deliberately separate concepts:

1. **Localized input text** — what the user typed.
2. **Canonical decimal string** — locale-independent, normalized decimal representation used for validation/review.
3. **Exact minor-unit integer / contract value** — derived from canonical decimal + explicit asset precision only at the service/transaction adapter.

Rules:

- determine decimal and grouping symbols from an explicitly supported locale policy; never strip punctuation blindly
- reject malformed grouping and ambiguous mixed separators
- normalize sign, leading zeros, and trailing zeros deterministically
- reject negative values for deposit/withdraw
- reject precision greater than the explicit asset scale unless a documented rounding policy says otherwise
- prefer rejection over implicit rounding for transaction-entry fields
- compare amount vs balance using exact decimal/minor-unit arithmetic, not `Number`
- preserve the canonical decimal through review and receipt
- format display from the canonical amount using `Intl.NumberFormat` only at the presentation edge
- never parse the displayed/localized string back to decide what to submit
- make mock services accept the same canonical/exact type expected of a future live adapter

No new decimal dependency is required if exact scale conversion can be implemented safely with validated strings + `BigInt`; if a decimal library is chosen, pin it and keep the conversion boundary explicit.

## Migration scope

A focused repair should cover all transaction-entry routes together:

- AmountInput
- DepositForm
- WithdrawForm
- DepositWizard
- WithdrawWizard
- validation
- share preview inputs/output reconciliation
- mock vault service amount contract
- balances/positions used for insufficient-funds comparisons
- display formatter boundary

Avoid broad conversion of unrelated chart/APY/UI statistics unless needed by acceptance.

## Required tests after assignment

### Locale grammar matrix

At minimum prove explicit behavior for:

- current supported `en-US`: `1,234.56`
- decimal-comma hostile fixtures such as `1.234,56` / `1 234,56` or narrow-NBSP forms according to the parser's deliberately declared locale set
- mixed separators
- malformed grouping (`12,34.56` under en-US)
- repeated decimal symbols
- leading/trailing whitespace
- plus/minus signs
- bare separator
- exponent notation (reject unless intentionally supported)

Do not label a locale product-supported merely because it appears in a hostile parser test.

### Precision/boundary matrix

For each configured asset precision:

- zero
- negative
- minimum positive unit
- one sub-unit below minimum
- precision exactly N
- precision N+1
- exact available balance
- balance + one minimum unit
- very large canonical value within declared service limits
- long fractional input that binary Number would round
- leading/trailing zeros
- MAX action

### Round-trip/property invariants

For every supported locale and generated valid canonical amount:

`parseLocalized(formatCanonical(canonical, locale), locale) == canonical_normalized`

Also prove:

- display formatting never changes the canonical submit value
- form and wizard paths produce the same canonical amount for equivalent input
- review amount == receipt canonical amount == service amount
- invalid/excess-precision input never reaches `vaultService.deposit/withdraw`
- no test relies on a funded wallet or mainnet

## Application draft

> I checked current `main@f0ce8b799ded8f8e4cc158069d1940b1f822eb17` before applying. The earlier “numeric parsing” commit only taught the parser to remove commas before `Number()`; the current transaction boundary is still lossy. `AmountInput` hard-codes en-US and treats every comma as grouping, so decimal-comma input can change meaning, while shared validation and share previews still convert through JavaScript Number. Both DepositForm/WithdrawForm explicitly call `Number(amount)` before the service, and both multi-step wizards bypass AmountInput entirely and do the same conversion. Display formatting is separately fixed to two decimals, so review text can also diverge from higher-precision input.
>
> After assignment I would introduce one explicit localized-input -> canonical-decimal -> exact-minor-unit boundary, with asset precision supplied by configuration/contract metadata rather than guessed from symbols. I would route both forms and wizards through it, compare balances exactly, keep display Intl formatting presentation-only, and make the mock service accept the same exact amount contract a future live adapter will need. Tests would cover locale grammar, malformed separators, minimum units, exact balance/balance+1 unit, N/N+1 precision, long fractional values, MAX, and property round trips that prove the displayed/reviewed/submitted amount stays identical.
>
> I will wait for official assignment before assignment-dependent upstream changes.

No provider submission success is claimed in this packet.

## Authority boundary

No upstream source, provider assignment, wallet, reward, payment, or transaction is changed. All transaction-path verification is limited to deterministic mocks/local fixtures/testnet where applicable.
