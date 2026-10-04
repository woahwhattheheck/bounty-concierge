## Problem

Financing already exposes `withdraw_offer(offer_id, lender)`, but the requested `cancel_offer(offer_id)` API is missing and the withdrawal path can mutate a pending offer after its invoice has been financed.

## Change

- Add `cancel_offer(offer_id)`, authenticating the offer's stored lender.
- Keep the existing explicit-lender withdrawal ABI and route both entrypoints through one cancellation transition.
- Require the offer and linked invoice to be Pending before changing state.
- Preserve the Rejected historical status, pending-offer view, negotiation closure and existing `off_wdr` event; decrement the lender's pending count once.
- Document panic conditions and update CHANGELOG.

The six new contract tests cover the authorized lender path and event payload, wrong-wallet authorization, accepted offers, both APIs against a financed invoice, a second cancellation, and pause behavior. They also check unchanged balances and registry state.

## Validation

25/25 focused cancellation, withdrawal and negotiation tests passed on Rust 1.99.0, with 0 failures or ignored cases; 72 unrelated tests were filtered out. Clippy passed on wasm32v1-none, and the identical production source built an 85,670-byte release WASM. All commands used the unchanged lockfile with `--locked`.

[Focused acceptance](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37206867133) · [Clippy/release build](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37206584756).

Closes #77.
