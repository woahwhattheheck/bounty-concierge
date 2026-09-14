# Offline wallet-history completeness

A directly saved canonical RustChain `/wallet/history` response is accepted for
offline settlement only when it is one complete bounded snapshot:

- `ok` is exactly `true`;
- `miner_id` exactly matches the settlement wallet;
- every `transactions` member is an object;
- `total` is a non-boolean integer;
- `total == len(transactions)`; and
- the row count does not exceed the settlement limit.

The exact-count requirement is an authority boundary. Offline reconciliation
cannot fetch omitted pages, so a first page carrying `total > len(transactions)`
must not stand for complete wallet history. Otherwise a later confirmed payment
could be absent while the report incorrectly remains partial or unverified.

Normalized operator captures keep their existing bounded-list contract. Online
provider reads continue to use the established paginated reader and reject page
gaps, total drift, duplicate rows, and provider-wallet mismatch.

This gate does not prove that any bound row pays a specific work item. Wallet
provenance, transaction identity, merge chronology, explicit row binding, and
amount/currency checks remain independent requirements. It performs no wallet,
provider, sponsor, payment, cash, or revenue mutation.
