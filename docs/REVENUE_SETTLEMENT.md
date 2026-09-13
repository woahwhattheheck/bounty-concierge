# Revenue settlement evidence

`concierge.revenue_closeout` deliberately stops at `cash_status=not_inferred`: merge state and advertised reward are not payment evidence. `concierge.revenue_settlement` is the next custody boundary. It can turn that status into `partially_verified` or `verified_paid`, but only when an operator explicitly binds exact RustChain wallet-history rows to a merged closeout item.

## Workflow

1. Produce a closeout snapshot:

   `python -m concierge.revenue_closeout closeout-manifest.json --json > closeout.json`

2. Capture wallet history, or allow the settlement command to read it through the existing authenticated payout tracker. A captured history file uses:

   ```json
   {"schema_version": 1, "items": [{"amount": 10, "to": "wallet-name", "timestamp": "..."}]}
   ```

   Mixed wallet history is expected. Mining rewards and other unrelated rows remain fingerprintable, but they are not treated as settlement evidence unless explicitly selected—and non-payment/outgoing rows remain unbindable.

3. Fingerprint the exact history rows with `history_row_sha256()` and create an operator-owned binding file:

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

   Omit `--history` to query the configured RustChain node through `payout_tracker.check_history()`.

## Authority boundaries

The reconciler never sends a transfer, contacts a sponsor, changes a claim, or treats merge state as cash. It does not guess matches by amount or timestamp. A history row is usable only when it is explicitly selected by SHA-256, is terminal/confirmed under the existing payout-history contract, targets the expected wallet, is not an outgoing or self-funded transfer, and contributes no more than the advertised RTC amount. The same history row cannot settle two closeout items.

Unrelated history rows do not have to satisfy payment semantics merely to coexist in a real wallet ledger. Payment semantics are enforced only for rows an operator explicitly binds to paid work; this avoids both false rejection of normal reward rows and heuristic attribution of unrelated cash.

`verified_paid` means only that the explicitly bound wallet-history evidence exactly matches the advertised RTC amount. It does not infer contract acceptance, tax treatment, fiat value, or profit.
