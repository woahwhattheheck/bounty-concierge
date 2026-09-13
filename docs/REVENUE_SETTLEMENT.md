# Revenue settlement evidence

`concierge.revenue_closeout` deliberately stops at `cash_status=not_inferred`: merge state and advertised reward are not payment evidence. `concierge.revenue_settlement` is the next custody boundary. It can turn that status into `partially_verified` or `verified_paid`, but only when an operator explicitly binds exact RustChain wallet-history rows to one merged closeout item.

## Wallet provenance is part of the evidence

Current RustChain `/wallet/history` responses identify the queried wallet in the canonical envelope:

```json
{
  "ok": true,
  "miner_id": "aliceRTC",
  "transactions": [
    {"type": "transfer_in", "amount": 10, "from": "treasury", "tx_hash": "..."}
  ],
  "total": 1
}
```

A canonical `transfer_in` does not need a row-level `to`: its recipient is the exact wallet already validated by the history envelope (`miner_id == queried wallet`). Settlement therefore binds every row fingerprint to both the raw row and that wallet provenance. Copying identical row JSON into a different wallet capture produces a different evidence identity.

Canonical `reward`, `ledger`, and `transfer_out` rows are never incoming bounty-cash evidence. Legacy typeless history rows remain compatible only when the row itself explicitly names the expected recipient.

## Workflow

1. Produce a closeout snapshot:

   `python -m concierge.revenue_closeout closeout-manifest.json --json > closeout.json`

2. Obtain wallet history. Online reconciliation performs a read-only canonical `/wallet/history` query using the payout tracker's existing node/TLS configuration, requests explicit `limit`/`offset` pages, requires one stable `total`, and preserves the validated `miner_id` instead of reducing the response to bare rows. Legacy online wrappers are refused for settlement because they do not carry provider wallet provenance. For offline reconciliation, preserve wallet provenance in one of these forms:

   Saved canonical RustChain response:

   ```json
   {"ok": true, "miner_id": "aliceRTC", "transactions": [], "total": 0}
   ```

   Normalized capture:

   ```json
   {
     "schema_version": 1,
     "source": "rustchain_wallet_history",
     "wallet": "aliceRTC",
     "items": []
   }
   ```

   Historical `{schema_version: 1, items: [...]}` files without wallet/source provenance are intentionally refused. A free `--wallet` argument must not reinterpret an unbound capture.

3. Fingerprint selected rows with `history_row_sha256(row, wallet=<captured-wallet>)` and create an operator-owned binding file:

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

4. Reconcile online:

   `python -m concierge.revenue_settlement closeout.json bindings.json --wallet aliceRTC --json`

   Or against an offline capture:

   `python -m concierge.revenue_settlement closeout.json bindings.json --wallet aliceRTC --history history.json --json`

## Authority boundaries

The reconciler never sends a transfer, contacts a sponsor, changes a claim, or treats merge state as cash. It does not guess matches by amount or timestamp. A selected row is usable only when its evidence fingerprint is bound to the exact wallet, the history provenance equals the settlement wallet, the row is a canonical incoming transfer (or an offline/legacy typeless row with an explicit matching recipient), the row is terminal/confirmed, it is not outgoing or self-funded, and its exact RTC amount fits within the advertised amount. One row cannot settle two closeout items in the same reconciliation. Online settlement additionally rejects provider wallet mismatch, total drift, incomplete pages, and duplicate indistinguishable rows across the canonical pagination snapshot.

Mixed wallet history is expected. Unrelated reward/ledger/outgoing rows stay fingerprintable for audit but are classified unbindable instead of poisoning valid incoming evidence elsewhere in the same history.

Settlement sums and cash summaries use bounded high-precision local decimal arithmetic rather than Python's ambient 28-digit context, so accepted 30-digit values and 18 fractional places are compared and aggregated exactly.

`verified_paid` means only that explicitly bound wallet-history evidence exactly matches the advertised RTC amount under these rules. It does not infer contract acceptance, tax treatment, fiat value, profit, or cryptographic authenticity of an operator-supplied offline capture.
