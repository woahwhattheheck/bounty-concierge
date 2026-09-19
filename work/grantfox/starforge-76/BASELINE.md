# GrantFox baseline — StarsForges/StarForge #76

Operation: `GFOX2-20260919-088/R-path-payment-source-architecture`  
Worker: ZZ-Sol-Forge · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream: `StarsForges/StarForge`  
Pinned upstream branch: `master`  
Pinned upstream head: `c2311c6cfbe25ab841ad6a18be60df0fe4f2fe61`  
Issue: https://github.com/StarsForges/StarForge/issues/76

## Authority fence

At observation time issue #76 is OPEN and has no GitHub assignee. The connected GitHub installation exposes the upstream repository as pull-only (`pull=true`, `push=false`). The exact GrantFox Slack work ID had no competing TAKE/PROGRESS/DONE when this lane was claimed.

This packet is a pre-assignment source/architecture audit. It does not:
- submit a provider application;
- claim provider assignment, reward, payment, or eligibility;
- mutate upstream;
- submit a live payment;
- use the funded payout wallet.

## Issue bar

#76 asks for a complete path-payment feature track:
- Horizon strict-send and strict-receive route discovery;
- typed asset and route models;
- deterministic ranking;
- slippage-safe transaction construction;
- destination/memo/preflight safety;
- quote freshness and refresh;
- confirmation, dry-run, offline envelope export;
- software and hardware signing;
- commands `payment paths`, `quote`, `send-strict`, `receive-strict`, `inspect`, and `watch`;
- stable JSON automation output;
- precision, stale-route, no-route, trustline, partial-response and XDR tests;
- at least 1,000 meaningful production lines;
- final repository gates on the exact merge head.

The current repository has reusable Horizon, CLI confirmation, amount, XDR, signer, hardware-wallet and private-file primitives. It also has older payment scaffolding that must **not** be mistaken for production-safe transaction construction.

## Critical finding 1 — existing payment XDR and signing are explicitly mock scaffolding

Pinned:
`src/utils/horizon.rs@bba08a81073a48d8bbb4d49e6d6dfccabdc8d252`

The Horizon client itself has useful production-shaped behavior:
- 5-second connection timeout;
- 30-second request timeout;
- bounded retries for transient failures;
- typed account and fee fetches;
- contextual Horizon error formatting.

But the existing payment transaction path is not a safe implementation base for #76:

- `build_and_simulate_payment()` states that production should use `stellar-xdr`, then delegates to a mock builder.
- `build_payment_transaction_xdr()` encodes a text string beginning `mock_payment_tx_` as base64.
- `build_batch_transaction_xdr()` does the same for batches.
- `sign_transaction_xdr()` creates a text value containing the first eight secret-key characters and base64-encodes it instead of signing a Stellar transaction signature payload.
- `build_and_simulate_payment()` does not submit a real transaction simulation; it returns a fixed fee estimate.
- sequence-rebuild and fee-bump retry helpers operate on those mock encodings.

A path-payment implementation must not add `PathPaymentStrictSend` or `PathPaymentStrictReceive` by extending these mock builders.

## Critical finding 2 — real Stellar-XDR and signature code already exists elsewhere

Pinned:
- `Cargo.toml@e2bd210dd1e5e4f4ff8170d3e9dd25cd73944d53`
- `src/signer_rotation/xdr.rs@4db85c7dbfd177d4cf370784238ac2cddffca43c`

The repository already depends on:
- `stellar-xdr`;
- `stellar-strkey`;
- `ed25519-dalek`;
- `base64`.

The signer-rotation XDR layer already demonstrates:
- real `stellar_xdr::curr::Transaction` construction;
- real `Operation` and `Memo` encoding;
- base64 XDR round trips;
- transaction signature payload construction using SHA-256(network passphrase);
- Ed25519 signatures and Stellar signature hints;
- duplicate-signature detection;
- body-hash verification;
- signature verification;
- hardware signing integration.

That machinery should be extracted into a reusable classic-transaction envelope boundary or reused through a small generic adapter. #76 should not build a second incompatible signing stack and should not use the old mock signer.

## Critical finding 3 — current amount checks use floating point

Pinned:
- `src/commands/tx.rs@68c524e483b897c74c510fda15bf9b3aa34af327`
- `src/utils/config/mod.rs@9756f4a5cf47f13026de79becf010b19dd577a21`

Current `tx send`:
- calls `config::validate_amount()`, which returns `f64`;
- parses XLM balance strings to `f64`;
- compares balance and fee using floating-point arithmetic.

Path-payment quoting, route ordering, and slippage must not use binary floating point.

Reusable exact-domain evidence already exists:
- `src/token/amount.rs@c9ac77037ef882a4a9080761fbb4ac18bb7abd3f` parses decimal strings into integer smallest units;
- `src/sep31/domain.rs@b0c540b819d010127ad1be7766abdb5431b88e3e` validates positive decimal amounts with at most seven fractional digits and validates classic credit assets.

#76 should establish one shared or path-payment-specific exact Stellar amount type backed by stroops/7-decimal integer arithmetic, with checked conversion into the i64 range required by Stellar classic XDR.

## No current strict-path implementation was found

Current-source search at the pinned head found no implementation hits for:
- `strict-send`;
- `strict-receive`;
- Horizon `/paths/strict-*`;
- `PathPaymentStrictSend`;
- `PathPaymentStrictReceive`.

This means route discovery and path-operation construction are real residual work, while basic Horizon transport and cryptographic building blocks are reusable prior art.

## Current CLI payment seam

Pinned:
`src/commands/tx.rs@68c524e483b897c74c510fda15bf9b3aa34af327`

Existing `tx` owns:
- `send`;
- batch;
- history;
- fee display.

It already uses:
- wallet/network validation;
- source-account fetch;
- destination existence checks;
- budget pre-signing gate;
- confirmation summaries;
- explicit mainnet warnings.

However, #76 explicitly requires a coherent `starforge payment ...` command family. A distinct `commands/payment` boundary is preferable to hiding route behavior behind `tx send`. It can reuse shared Horizon/confirmation/budget helpers without inheriting the mock payment builder.

Pinned `src/main.rs@349e092c89432709304100a98448dfd862e01480` requires a new top-level command to be wired into:
- the Clap enum;
- dispatch;
- telemetry command-name mapping;
- machine-readable/banner suppression logic.

## Confirmation is reusable, but confirmation must bind to an immutable quote/plan

Pinned:
`src/utils/confirmation.rs@83429c4870ad0af495b44db119ccc05d23dfa353`

The current confirmation layer already supports:
- risk levels;
- structured operation summaries;
- `--yes`;
- dry-run;
- typed `yes` confirmation for high-risk actions.

Reuse it.

But path-payment safety adds a new invariant: if quote, route, sequence, fee, memo, slippage bound, destination preflight, network identity, or transaction body changes after confirmation, prior confirmation is invalid.

The current generic retry path in `utils/horizon.rs` can rebuild sequence or fee after submission failure. That behavior is unsafe for a path payment if it silently changes the confirmed body. For #76:
- stale sequence => rebuild plan;
- stale quote => refresh quote;
- changed plan => re-render summary and re-confirm;
- changed envelope => re-sign.

Do not silently mutate and submit a newly signed body under an earlier confirmation.

## Horizon freshness is a local safety policy, not an exchange guarantee

A path response is a network observation, not a firm price guarantee.

The CLI should record:
- `quoted_at`;
- policy-defined `accept_until`;
- network identity;
- exact requested amount;
- exact route and delivered/source amounts.

The acceptance window means only "StarForge will not reuse this observation after this time." It must not be described as a Horizon-guaranteed quote expiration.

The on-chain atomic price protection is:
- strict-send: exact source amount plus `dest_min`;
- strict-receive: exact destination amount plus `send_max`.

## Route ranking must be deterministic and exact

Avoid an opaque weighted floating-point score.

Recommended lexicographic ranking:

### Strict send
1. filter routes that violate hard user asset policy;
2. maximize exact destination amount;
3. prefer lower declared policy/risk penalty;
4. prefer fewer path hops;
5. stable lexicographic path-asset key.

### Strict receive
1. filter routes that violate hard user asset policy;
2. minimize exact source amount;
3. prefer lower declared policy/risk penalty;
4. prefer fewer path hops;
5. stable lexicographic path-asset key.

This ordering is reproducible in tests and stable JSON fixtures.

Do not call a route "low liquidity" solely because it has more hops. Only emit liquidity/price-impact warnings when backed by defined observations. Otherwise label the signal honestly as route complexity or policy risk.

## Slippage math

Use exact integer arithmetic with checked intermediates.

For a quoted strict-send destination amount `D` and tolerance `bps`:

`dest_min = floor(D × (10000 - bps) / 10000)`

For a quoted strict-receive source amount `S`:

`send_max = ceil(S × (10000 + bps) / 10000)`

Use a wider checked integer for multiplication and reject overflow before encoding the i64 Stellar amount.

The pre-sign summary and JSON output must show:
- quoted amount;
- tolerance;
- resulting exact on-chain bound.

## Destination safety is broader than "account exists"

Current `tx send` checks whether the destination account exists, and for native XLM can describe a CreateAccount-like path. A Stellar path-payment operation is different.

For a credit destination asset:
- destination account must exist;
- matching trustline must exist;
- relevant authorization state must permit receiving;
- selected asset code/issuer must match exactly.

For a native destination:
- destination account must exist for path-payment execution.

Do not silently substitute a different asset or convert a missing destination into a separate CreateAccount operation unless that behavior becomes an explicit reviewed feature.

The current account response model may need additional trustline authorization fields or a dedicated preflight response model.

## Asset and memo domain

Reuse the existing exact validation ideas without coupling route discovery to SEP-31 business semantics.

Suggested classic asset:
- native XLM;
- credit asset with validated 1–12 ASCII alphanumeric code and exact issuer.

Suggested memo:
- None;
- Text <= 28 bytes;
- Id(u64);
- Hash(32 bytes);
- Return(32 bytes), if the chosen Stellar-XDR version/command UX supports it.

The existing SEP-31 memo model does not include Return, so blindly reusing it would silently narrow Stellar transaction behavior.

## Hardware signer reality

Pinned:
`src/utils/hardware_wallet.rs@8a78006e1ec05cc98c1ff1e11af7353b1dd499f2`

Current hardware support:
- Ledger has Stellar APDU signing support.
- Trezor supports device detection/address derivation in this abstraction.
- the generic Trezor signing path explicitly returns an unsupported error for arbitrary-message signing.

#76 must not advertise hardware support more broadly than the implementation proves.

Safe choices:
1. support software signing plus Ledger and return an explicit unsupported capability for Trezor; or
2. add a correct Trezor Stellar-transaction-envelope signing implementation and test it through its supported API.

The acceptance criterion says hardware **or** software signer support; it does not justify faking unsupported devices.

## Offline envelope / file-security seam

Pinned:
`src/signer_rotation/store.rs@348d74a9e5c79c4e750519dbc617fd4f970d57d6`

This module already has:
- temporary-file write;
- create-new;
- flush/sync;
- atomic rename;
- cleanup on failure;
- Unix 0600 file permissions;
- version-aware loaders.

Reuse/extract these helpers for offline payment plans/envelopes.

An offline export should contain no secret key. It should bind:
- schema version;
- network identity/passphrase hash;
- quote/route digest;
- source/destination;
- exact amounts and on-chain bound;
- memo;
- expected source sequence;
- time bounds / local quote acceptance time;
- unsigned envelope XDR;
- transaction-body SHA-256;
- preflight summary;
- creation time.

On import/inspect/sign:
- validate schema;
- verify transaction-body hash;
- decode real XDR;
- compare decoded operation/memo/time bounds against the manifest;
- reject expired/stale plans before submission.

## Existing ceremony file caveat

`src/utils/ceremony.rs@db45c00a927519f587f5e6e8825a3231c158c516` contains strong transaction-body integrity checks and signature workflows, but its direct `save_ceremony_file()` uses a plain write. For #76's restrictive-file requirement, prefer the atomic private writer from signer-rotation storage.

## Stable machine output

#76 requires stable versioned JSON for automation.

Do not make terminal rendering the domain contract. Recommended response envelope:

```json
{
  "schema_version": 1,
  "command": "payment.quote",
  "network": "testnet",
  "result": {}
}
```

Domain/service methods should return typed data. The command layer chooses human vs JSON rendering.

Add `payment::is_machine_readable()` to the same main-program output-suppression path used by existing structured commands.

## Source pins

| Surface | Blob SHA |
| --- | --- |
| Cargo.toml | `e2bd210dd1e5e4f4ff8170d3e9dd25cd73944d53` |
| main CLI | `349e092c89432709304100a98448dfd862e01480` |
| commands module | `e0a1165d3307473c96d1a4870894a151bdd4fe67` |
| tx CLI | `68c524e483b897c74c510fda15bf9b3aa34af327` |
| Horizon/payment utilities | `bba08a81073a48d8bbb4d49e6d6dfccabdc8d252` |
| config validation | `9756f4a5cf47f13026de79becf010b19dd577a21` |
| exact token amount | `c9ac77037ef882a4a9080761fbb4ac18bb7abd3f` |
| SEP-31 domain | `b0c540b819d010127ad1be7766abdb5431b88e3e` |
| confirmation | `83429c4870ad0af495b44db119ccc05d23dfa353` |
| real signer-rotation XDR | `4db85c7dbfd177d4cf370784238ac2cddffca43c` |
| private atomic store | `348d74a9e5c79c4e750519dbc617fd4f970d57d6` |
| ceremony workflow | `db45c00a927519f587f5e6e8825a3231c158c516` |
| hardware wallet | `8a78006e1ec05cc98c1ff1e11af7353b1dd499f2` |

## Disposition

**READY_FOR_ASSIGNMENT_WITH_REAL-XDR EXTRACTION AS A FIRST-CLASS DEPENDENCY.**

The feature should not begin by adding path endpoints to the existing mock payment builder. It should first establish exact amount/asset/quote domain types and a real reusable classic-transaction envelope/signing boundary, then compose discovery, deterministic ranking, preflight, offline plan, signer, and execution around those contracts.

The concrete implementation architecture and hostile test plan are in `ARCHITECTURE.md`.
