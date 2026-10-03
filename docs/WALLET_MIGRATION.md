# Continue a Discord wallet migration

Start with the existing command:

```bash
concierge wallet migrate --user DISCORD_ID --to WALLET_NAME --json
```

The command reads the source balance and retains the exact source, target,
amount, node, Discord database configuration, and idempotency key in SQLite
before requesting a credit. The request includes the node's required `reason`
and `idempotency_key`. Its returned transaction hash and pending ID are saved
before proceeding.

A pending acknowledgement exits **2**. After the node confirms the transfer,
continue the same attempt:

```bash
concierge wallet migrate --user DISCORD_ID --resume --json
concierge wallet migrate --history --json
```

`--resume` uses the saved request. If its response was lost, it resends that
same idempotency key so the node can return the existing transfer. Once its
hash is known, it reads `/wallet/tx/HASH`. A successful response must bind that
hash, report `confirmed`, and contain at least one confirmation before the
command debits Discord.

The Discord debit and its transaction row are one SQLite transaction. The
saved attempt key also identifies that debit: replay checks the existing row
and returns it without subtracting the balance again. This covers an
interrupted debit acknowledgement and concurrent resumptions of the same key.
Completion is printed only after the local completion record commits.

## Retained states

| State | Next action |
| --- | --- |
| `prepared` / `transfer_unknown` | Resume the saved request with the same key. |
| `pending` | Resume after the node reports confirmation. |
| `confirmed` / `debit_pending` / `partial` | Resume to reconcile the same Discord debit. |
| `completed` | The retained attempt is finished; resume returns it without another provider action. |
| `failed` | Inspect the failed transfer and its existing record; it is never treated as a completed migration. |

Errors exit **1**. JSON mode preserves the retained attempt and status instead
of mixing prose into the object. A balance lookup or local persistence failure
also exits nonzero. `--force` retains its meaning of starting another migration
after a completed one; it cannot replace an unresolved attempt. `--resume`
cannot change the target or use a different node or Discord database.

The default journal is `~/.concierge/migrations.db`. Set
`CONCIERGE_STATE_DIR` to select another durable state directory. Preserve this
database when moving the command between environments. Separate databases
cannot coordinate two independently started attempts; concurrent operators
must use the same retained attempt and state directory. The original
`migrations` table remains the current/history view, while
`migration_attempts` retains each new attempt across forced reruns.

Older unresolved rows do not contain a recoverable provider key. They remain
visible and require reconciliation of their existing transfer; the command
does not invent a key or replay their credit.

Both new and resume commands support `--dry-run --json` for an argument-only
plan that performs no reads or writes.

The transfer request and status fields follow the first-party node's
[`wallet_transfer_v2` and `api_wallet_tx_status` implementations](https://github.com/Scottcjn/Rustchain/blob/c3da95d5e6163d7fe06b9d12a7735976392f2318/node/rustchain_v2_integrated_v2.2.1_rip200.py).
