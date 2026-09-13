# Durable settlement registry

`concierge.settlement_registry` adds durable **cross-run cash-evidence custody** on top of
`concierge.revenue_settlement`.

`revenue_settlement` already refuses to count one wallet-history row or explicit transaction
identity twice inside a single reconciliation. That protection is intentionally process-local:
a later process can otherwise load the same confirmed transfer again and bind it to a different
closeout item. The registry closes that gap by retaining transaction-to-claim custody across
independent runs.

## Authority boundary

The registry is bookkeeping and evidence only. It never:

- mutates a RustChain wallet or provider;
- sends or receives a transfer;
- initiates a payout;
- contacts a sponsor;
- submits a bounty claim;
- converts currency; or
- recognizes accounting revenue.

Every state and receipt carries those authority flags as `false`, and verification rejects a
resealed object that changes the ceiling.

The registry also does **not** make wallet-history data more authentic than
`revenue_settlement` does. It first runs the established settlement verifier and only then retains
the exact canonical transaction identities that verifier consumed.

## What is retained

A registry is bound to one exact:

- network (`rustchain`);
- recipient wallet; and
- wallet-history source identity.

Each claim retains:

- exact repository + PR identity;
- RTC advertised amount;
- monotonically verified amount;
- `partially_verified` or `verified_paid`; and
- the complete sorted set of canonical transaction IDs used so far.

Each transaction retains:

- canonical provider `tx_hash`;
- exact wallet-history row SHA-256;
- bound repository + PR;
- RTC amount.

One transaction identity can occur only once in the entire registry. Re-presenting the same
transaction for another claim, with another history-row hash, or with another amount fails closed.

Registry v1 deliberately accepts only canonical `type="transfer_in"` evidence with an explicit
`tx_hash`. Legacy typeless rows can still be used by ordinary `revenue_settlement` where supported,
but they do not receive durable cross-run transaction authority.

## Monotonic updates

Create an in-memory genesis state:

```python
from concierge import settlement_registry as registry

state = registry.new_registry(
    wallet="rtc-recipient",
    history_source="captured_wallet",
)
```

Apply one already-merge-gated closeout item with exact wallet history and the same binding format
used by `revenue_settlement`:

```python
next_state, receipt = registry.reconcile_and_apply(
    state,
    closeout_item,
    history_rows,
    payment_binding,
    wallet="rtc-recipient",
    history_wallet="rtc-recipient",
    history_source="captured_wallet",
    expected_state_sha256=state["state_sha256"],
)
```

`expected_state_sha256` is a compare-and-swap fence. A later update must present the digest from
the independently retained previous state/receipt. Do not derive the expected digest only from the
file you are about to mutate if rollback detection matters.

For an existing claim, every previously custodied transaction must still be present in the new
reconciliation. Evidence can advance from (for example) 4 RTC to 10 RTC by adding a second
confirmed transaction, but a later run cannot omit the original 4 RTC transaction or move it to
another claim.

An exact replay is idempotent: the generation and state digest do not advance.

## Content-addressed state and receipts

State JSON is canonicalized and carries `state_sha256`, computed over every state field except the
digest itself. Each successful or idempotent application returns a deterministic receipt carrying:

- predecessor state digest;
- resulting state digest and generation;
- exact claim identity;
- verified amount/status;
- transaction IDs; and
- the same all-false authority ceiling.

`verify_registry()` and `verify_receipt()` reject digest tampering, type aliases such as
`False` for generation `0`, duplicate transaction/claim identity, noncanonical amount text,
authority escalation, and internal aggregate mismatches.

The predecessor field is a one-step chain link, not an immutable external ledger. Durable rollback
detection therefore depends on retaining the latest accepted state digest or receipt outside the
mutable registry file.

## File-backed cross-process use

Initialize once:

```python
state = registry.initialize_registry_file(
    "/var/lib/concierge/settlements.json",
    wallet="rtc-recipient",
    history_source="captured_wallet",
)
```

Commit through the file helper:

```python
next_state, receipt = registry.commit_claim_file(
    "/var/lib/concierge/settlements.json",
    closeout_item,
    history_rows,
    payment_binding,
    wallet="rtc-recipient",
    history_wallet="rtc-recipient",
    history_source="captured_wallet",
    expected_state_sha256=retained_previous_sha256,
)
```

Readers and writers share one exclusive sibling lock (`settlements.json.lock`). A `reading` or
`writing` lock is never auto-broken: this module cannot prove another process is dead.

Writes use a same-directory temporary file, file `fsync`, atomic `os.replace`, parent-directory
`fsync`, and exact readback verification.

### Ambiguous commit rule

The visible commit point is `os.replace`. If any step fails **after** that point (for example parent
directory `fsync` or readback), the function raises `SettlementRegistryAmbiguousCommit` and keeps
the registry on HOLD behind an `ambiguous` lock marker. `load_registry_file()` refuses to grant live
authority while any lock exists.

Operators can inspect verified bytes without granting authority:

```python
audit = registry.audit_registry_file(path)
```

After independently deciding that the exact observed state is the state to retain, an operator can
clear only a completed ambiguity marker:

```python
registry.clear_reconciled_lock(
    path,
    expected_state_sha256=audit["state"]["state_sha256"],
)
```

That helper refuses `reading` or `writing` locks and refuses an ambiguity marker whose candidate
digest differs from the accepted state. A crashed `reading`/`writing` lock therefore requires
manual process-level recovery rather than an unsafe stale-lock timeout.

## Strict JSON

File loads reject:

- duplicate object keys at any nesting level;
- `NaN` / `Infinity`;
- floating-point JSON numbers;
- malformed UTF-8;
- files over the bounded size limit; and
- schema fields outside the exact v1 domain.

Amounts are retained as canonical decimal strings, avoiding ambient binary-float authority.

## Failure examples that must stay red

The test suite keeps these cases fail-closed:

- one `tx_hash` applied to two different PRs in separate updates;
- same `tx_hash` reappearing with changed row hash or amount;
- a partial claim later omitting already-custodied evidence;
- stale compare-and-swap digest;
- wallet/history-source rebinding;
- legacy typeless evidence entering durable custody;
- content or receipt tamper;
- duplicate-key/non-finite JSON;
- post-rename failure followed by an attempted live read; and
- automatic clearing of an active/unknown writer lock.

This layer complements, rather than replaces, the existing closeout and wallet-history verifier.
