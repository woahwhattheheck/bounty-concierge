# Offline wallet-history completeness

A directly saved canonical RustChain `/wallet/history` response is accepted for
offline settlement only when it is one complete bounded snapshot:

- `ok` is exactly `true`;
- `miner_id` exactly matches the settlement wallet;
- every `transactions` member is an ordinary JSON object after one inert snapshot;
- `total` is a non-boolean integer;
- `total == len(transactions)`; and
- the row count does not exceed the settlement limit.

The exact-count requirement is an authority boundary. Offline reconciliation
cannot fetch omitted pages, so a first page carrying `total > len(transactions)`
must not stand for complete wallet history. Otherwise a later confirmed payment
could be absent while the report incorrectly remains partial or unverified.

The complete-snapshot check lives in the single authoritative
`concierge._revenue_settlement_core` implementation. The historical
`concierge._revenue_settlement_base` name is an alias of that exact module, so
import order and direct `python -m concierge._revenue_settlement_core` execution
cannot recover a weaker loader.

Before interpreting either supported offline format, the core serializes the
caller value as bounded canonical JSON and reparses it into ordinary built-in
containers. The resulting inert snapshot is the only semantic generation read.
Caller-defined `dict`/`list` subclasses therefore cannot present one generation
to a preflight check and a different generation to settlement logic.

Normalized operator captures keep their existing bounded-list contract. They
remain an explicitly operator-authored format, not a claim of provider-complete
pagination. Online provider reads continue to use the established paginated
reader and reject page gaps, total drift, duplicate rows, and provider-wallet
mismatch.

This gate does not prove that any bound row pays a specific work item. Wallet
provenance, transaction identity, merge chronology, explicit row binding, and
amount/currency checks remain independent requirements. It performs no wallet,
provider, sponsor, payment, cash, or revenue mutation.
