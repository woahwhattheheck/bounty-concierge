# InvoFi lender cancellation candidate

This candidate completes the remaining source scope in [Stellar-VaultLink/invofi-contracts#77](https://github.com/Stellar-VaultLink/invofi-contracts/issues/77), under the existing INVOFI77-46139 intake for woahwhattheheck.

## Behavior

`cancel_offer(offer_id)` authenticates the lender stored on the offer. The existing `withdraw_offer(offer_id, lender)` signature remains available. Both entrypoints share the same transition and reject a non-Pending offer or invoice before any cancellation state changes.

A successful cancellation retains the historical offer as Rejected, removes it from pending-offer queries, decrements the lender's pending count once, closes any open negotiation and emits the existing `off_wdr` event. Registry state, account balances and cumulative offered/accepted totals remain intact.

Only these upstream files change:

- `financing/src/lib.rs`
- `financing/src/test.rs`
- `CHANGELOG.md`

The two Rust files are formatted using the runner's rustfmt; their original baseline formatting differences are included in those same files. The changelog retains its original CRLF line endings.

## Validation and source identity

Upstream base: `54e63c835c7679dd32e9f99e0d9f67743b0d1d01`.

The attached `SOURCES.json` records the exact final postimage Git hashes. The workflow checks the upstream base and candidate hashes, then retains the exact formatted files it executes. `RECEIPT.json` records the result and `VALIDATION.log` retains the relevant native job output. The complete source, manifest, commands, and result are available without the original executor.

The focused test command selects cancellation, withdrawal and negotiation cases in the financing crate; it excludes unrelated property workloads. Validation also runs Clippy against the real wasm32v1-none contract target and compiles the release financing WASM. There is no network deployment or token transaction.

## Results

- **25/25 focused tests passed:** six new cancellation cases and 19 existing withdrawal/negotiation cases; 0 failed or ignored, 72 unrelated tests filtered out. Test execution 0.23 seconds after 40.81 seconds of compilation.
- **Clippy passed** on the actual `wasm32v1-none` target.
- **Release WASM built:** 85,670 bytes, SHA-256 `7e186b0de0a56805c0be421696094c0cc2b9d35263ff7a607d7b745a75bdd6ba`.
- Runtime: Ubuntu 24.04, Rust 1.99.0, Cargo 1.99.0, Soroban SDK 22.0.11; unchanged lockfile and `--locked` commands.

[Final focused acceptance](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37206867133) checks the exact final three postimages. [Clippy and release build](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37206584756) tested the identical contract implementation; that run exposed a new test's invalid direct comparison of raw Soroban values. The final test compares the decoded event symbols and lender address. Clippy and WASM were not rerun for that test-only correction.

## Apply after the existing assignment step

Start from the pinned upstream base and copy the three corresponding files from `postimages/` onto their original paths. Keep the existing lender-withdrawal ABI and original issue/application identity.

**Maintainer assignment remains pending.** The repository's require-assignment workflow gates nondraft review/merge and permits draft work. This source packet continues the original intake; it does not create an upstream PR, replacement claimant, new publication queue, award or payment claim. GrantFox Third Campaign compensation is conditional, with no fixed dollar amount established for this issue.
