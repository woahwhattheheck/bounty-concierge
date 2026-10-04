# InvoFi issue 77 cancellation candidate

This is the three-file candidate for [Stellar-VaultLink/invofi-contracts#77](https://github.com/Stellar-VaultLink/invofi-contracts/issues/77), continuing existing intake INVOFI77-46139 for woahwhattheheck.

Upstream base: `54e63c835c7679dd32e9f99e0d9f67743b0d1d01`.

The candidate adds lender-authorized `cancel_offer(offer_id)`, preserves `withdraw_offer(offer_id, lender)`, and requires both the offer and its invoice to remain Pending before either transition. The existing event and negotiation behavior remain, and pending counts are updated. It includes six focused cancellation cases plus the change log.

Validation is pending in the attached public Ubuntu workflow. The runner will format only the two changed Rust files, retain their exact postimages and Git hashes, execute cancellation/withdrawal/negotiation tests, run the package Clippy check on wasm32v1-none, and build the release financing WASM. It does not deploy or transact.

**Assignment remains pending.** The repository requires maintainer assignment before nondraft review/merge. The existing intake owns that request. This candidate is not an assigned issue, upstream PR, accepted contribution, fixed reward, or payment claim. No additional publication queue is created.
