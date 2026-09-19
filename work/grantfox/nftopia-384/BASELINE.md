# GrantFox baseline — NFTopia-Foundation/nftopia-stellar #384

Operation: `GFOX3-20260919-nftopia-384/R-contract-path-census`  
Worker: ZZ-Rook-73 · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream head: `535c03fd8ee344ad73282d4aef76139befc4b24a`

## Canonical issue / authority

- GitHub: https://github.com/NFTopia-Foundation/nftopia-stellar/issues/384
- GrantFox: https://contribute.grantfox.xyz/org/NFTopia-Foundation/repo/nftopia-stellar/issue/384
- State observed: OPEN, GitHub-unassigned; GrantFox Unassigned
- Labels: Rust, Soroban, GrantFox, Maybe Rewarded, GrantFox OSS, Third Campaign
- Existing issue comments: 4 applications
- Fresh PR search for issue / zero-dust terms: no matching carrier surfaced
- Connector permission: upstream readable, not writable (`pull=true`, `push=false`)
- No upstream code, assignment, reward, wallet, or payment state is changed by this packet.

## Current-main drift: several issue premises are already fixed

Pinned `auction_engine.rs` blob: `2b4070876eedb731e7560a42c6d81a25de1f0b6a`.

Current `validate_auction_params` already:
- rejects `starting_price <= 0`,
- rejects `reserve_price < 0` and `reserve_price > starting_price`,
- rejects `bid_increment <= 0`.

Current `place_bid` already calls `validate_bid_amount` before commitment storage / bid storage. With a new auction's `highest_bid == 0`, the first check `bid_amount <= highest_bid` rejects zero and negative bid amounts. The first bid must also meet `starting_price`, which itself is strictly positive.

Current test blob `2866535d36324e512f27cd6895a471c8c7016ea5` already contains:
- `test_create_auction_zero_price_fails`
- `test_bid_below_starting_price_fails`

So an assigned implementation should **not** duplicate those fixes or claim current direct bidding accepts zero.

## Residual high-value gap: reveal path bypasses amount validation

Pinned front-running blob: `e5a7d7b0b59f3183c2e9656177bf5f7ba4ef125d`.

`CommitRevealScheme::create_commitment` currently ignores `bidder`, `auction_id`, and `bid_amount` and returns only `salt.clone()`.

That means the stored commitment does not cryptographically bind the bidder, auction, or amount. `reveal_commitment` recomputes through the same function, so a matching salt can validate regardless of the revealed amount.

Separately, `AuctionEngine::reveal_bid` does **not** call `validate_bid_amount`. Its sequence is:

1. `reveal_commitment(..., bid_amount, salt)`
2. load auction
3. `process_direct_bid(..., bid_amount, ...)`
4. `transfer_tokens(..., bid_amount, ...)`
5. update stored committed bid

Because `process_direct_bid` refunds the displaced highest bidder before overwriting `highest_bid`, the reveal path needs the same economic validation fence as direct bids before any refund/state transition.

This is the strongest current-source connection to #384's commit/reveal zero/dust acceptance criteria.

## Residual storage-growth gap

Pinned `auction_store.rs` blob: `624ab896a0137777f73b1874682f0a11e0c01ba6`.

`AuctionStore::add_bid` unconditionally `push_back`s into the per-auction `Vec<Bid>`. There is no per-auction history cap in that path.

The issue's storage-griefing concern therefore remains materially open, but the correct bound should be chosen consistently with repository/Soroban resource policy rather than inventing an arbitrary number in isolation.

## Dust-policy nuance

Pinned `asset_utils.rs` blob: `a073d6edc410a3a94832987422b5145852d6cb0b`.

- `validate_payment_amount` rejects `amount <= 0`, but auction bidding does not currently use it.
- `transfer_tokens` rejects negative amounts but allows zero.
- Native XLM uses 7 decimals, and token decimals are queried dynamically.

The issue suggests a fixed `MINIMUM_BID_AMOUNT = 100_000` stroops. A fixed raw minimum may be valid if maintainers intend it as protocol policy, but token assets can have different decimal metadata. An assigned patch should confirm whether the minimum is:
- a protocol-wide raw-unit constant,
- asset-decimal-aware,
- or represented through auction configuration / starting-price semantics.

Do not silently bake an economic policy into a security patch without resolving that contract.

## Error/event constraint

Pinned `error.rs` blob: `09d5aa83f9d2f7f76b8b1b17341b9b665c3329fa`.

`SettlementError` is explicitly at the Soroban 50-case spec limit. The issue asks for "specific error events" for invalid zero/dust bids; do not casually add another `SettlementError` variant because the source warns that doing so fails the contract spec build. Reuse an existing error or introduce a separate error domain only if the public API actually requires it.

## Assignment-ready implementation order

1. Rebase to latest upstream main and reconfirm no overlapping #384 carrier.
2. Fix commitment construction so it binds at least bidder + auction_id + bid_amount + salt using a real deterministic digest/encoding supported by the contract.
3. In `reveal_bid`, run the same bid amount / increment / starting-price checks as the direct path **before** refunding any previous highest bidder or transferring funds.
4. Add hostile tests proving:
   - changing reveal amount with the same salt fails,
   - zero / negative reveal fails before refund/state mutation,
   - below-starting and below-increment reveal fails,
   - valid committed reveal succeeds.
5. Add a bounded bid-history policy with explicit tests if maintainers confirm that acceptance item remains in scope.
6. Resolve the absolute dust threshold as an explicit protocol/asset-units decision, then add per-auction-type coverage.
7. Preserve the already-working zero starting-price / negative reserve validations instead of rewriting them.
8. Run focused contract tests plus the repository's Rust formatting/lint/full test commands; report exact commands and baseline failures.

## Application-ready note

> I audited #384 against current `main@535c03fd8ee344ad73282d4aef76139befc4b24a` before applying. Some issue premises have already drifted: current auction creation rejects zero starting price and negative reserve price, and direct/commit `place_bid` rejects zero through `validate_bid_amount`. I would avoid duplicating those fixes.
>
> The current residual is sharper: `create_commitment` ignores bidder/auction/amount and returns only the salt, while `reveal_bid` bypasses `validate_bid_amount` before refund/state changes. `AuctionStore::add_bid` is also unbounded. I would first bind the commitment to the actual bid tuple, revalidate reveal amounts before effects, and add adversarial tests for amount substitution / zero / below-increment reveals. I would treat the suggested 100_000-stroop floor as an explicit protocol-unit decision rather than silently assuming the same raw threshold for every token asset. I will wait for assignment before modifying upstream.
