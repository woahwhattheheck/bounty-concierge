# Revenue settlement evidence

`concierge.revenue_closeout` deliberately stops at `cash_status=not_inferred`: merge state and an advertised reward are not payment evidence. `concierge.revenue_settlement` is the next custody boundary. It can turn that status into `partially_verified` or `verified_paid`, but only when an operator explicitly binds exact, canonical RustChain incoming-transfer evidence to a merged closeout item.

## Workflow

1. Produce a closeout snapshot:

   `python -m concierge.revenue_closeout closeout-manifest.json --json > closeout.json`

2. Capture the **raw canonical** RustChain `/wallet/history` response for the recipient wallet, or omit `--history` so the settlement command performs that read through the configured RustChain transport. A captured history file must preserve the provider envelope and wallet identity, for example:

   ```json
   {
     "ok": true,
     "miner_id": "wallet-name",
     "transactions": [
       {
         "type": "transfer_in",
         "amount": 10,
         "epoch": 200,
         "timestamp": 1772848800,
         "tx_hash": "6df5d4d25b6deef8f0b2e0fa726cecf1",
         "from": "sponsor-wallet"
       }
     ],
     "total": 1
   }
   ```

   Bare arrays, the old `{schema_version, items}` history wrapper, and caller-authored recipient aliases are not cash authority. The enclosing `miner_id` is the canonical recipient identity for `transfer_in` rows.

3. Fingerprint the exact incoming transaction **with the recipient wallet bound into the fingerprint** and create an operator-owned binding file:

   ```python
   history_row_sha256(transaction, wallet="wallet-name")
   ```

   ```json
   {
     "schema_version": 1,
     "items": [
       {
         "repo": "Sponsor/project",
         "pr": 123,
         "history_sha256s": ["<64-hex sha256>"]
       }
     ]
   }
   ```

4. Reconcile:

   `python -m concierge.revenue_settlement closeout.json bindings.json --wallet wallet-name --history history.json --json`

   Omit `--history` to query the configured RustChain node. The live path requires the current canonical `{ok, miner_id, transactions, total}` response and does not coerce legacy flat-array history into cash evidence.

## Authority boundaries

The reconciler never sends a transfer, contacts a sponsor, changes a claim, or treats merge state as cash. It does not guess matches by amount or timestamp.

A history row can contribute closeout cash only when all of these conditions hold:

- the history snapshot is the canonical RustChain envelope and its `miner_id` exactly matches `--wallet`;
- the row is a canonical `transfer_in` transaction with `from`, `epoch`, `timestamp`, and non-null `tx_hash` in the current provider shape;
- the operator explicitly selected the wallet-bound transaction fingerprint;
- the closeout item is merged and denominated in RTC;
- the selected incoming-transfer sum does not exceed the advertised amount; and
- no selected row is reused for another paid-work item.

`transfer_out`, `reward`, `ledger`, unknown transaction types, legacy/bare history, and arbitrary operator-authored JSON cannot settle a bounty. They may appear in a real heterogeneous history snapshot, but only canonical `transfer_in` rows are `cash_eligible`.

The reconciliation output includes `history_envelope_sha256` and repeats that snapshot digest on each payment-evidence record. `verified_paid` means only that explicitly bound canonical incoming-wallet evidence exactly matches the advertised RTC amount. It does not infer contract acceptance, tax treatment, fiat value, profit, or sponsor intent.
