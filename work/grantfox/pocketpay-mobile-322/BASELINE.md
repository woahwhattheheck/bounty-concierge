# Axionvera/pocketpay-mobile #322 — transaction-status refresh residual

## Disposition

**SOURCE-REAL, PARTIALLY IMPLEMENTED.** Do not rebuild the pending-transaction queue. The remaining issue is a narrower identity/status-authority defect plus a transaction-detail refresh action.

This is a pre-assignment source packet. It does not apply for, claim, assign, implement, submit, adjudicate, or assert payment for the GrantFox issue.

## Provider / issue snapshot

- Upstream: `Axionvera/pocketpay-mobile#322`, “Add mobile transaction status refresh action”.
- GitHub state at census: OPEN, 0 comments, no assignee.
- Labels: `Maybe Rewarded`, `GrantFox OSS`, `Official Campaign | FWC26`.
- Targeted open-PR search for `#322`: none observed.
- Exact Slack search immediately before TAKE returned only the broad FWC26 discovery-pool mention, with no TAKE / PROGRESS / DONE carrier for #322.
- Upstream connector repository metadata: `pull=true`, `push=false`.

## Canonical source pin

Current upstream `main`:

`c3a24abacb45030eb4fef41aabc46b312aaf54a3`

Pinned blobs:

- `src/store/walletStore.ts` — `6ee485a9c9d2ffe3bb80b1e70c4bf720fcfbec81`
- `app/transaction/[id].tsx` — `99113180ff6069476d518d9a8f67bce9eda3c5f2`
- `src/services/stellar.ts` — `3cbe4124bbefb3f7a6539fdaef2502f84921715f`
- `src/features/transactions/helpers.ts` — `67a53fad9ce9712dc4fa83b510f99855513c5944`
- `src/features/transactions/types.ts` — `467e0d842302f627b73e61a82c7e0b7948157efa`
- `src/components/PendingTransactionQueue.tsx` — `5832221364ce3bfc73be5f13d8f1805f43c06cb4`
- `src/components/TransactionListItem.tsx` — `6672b248a4c96b3460b4aefd1c821f5bbd082b4f`
- `app/(tabs)/history.tsx` — `052c0da3373ea7d7d91ddcaf19c3cee34de44cc4`
- `src/utils/paymentErrors.ts` — `e2ea5ba2748db82af1b19bb9be6d800fbd0b36e8`

## What already exists

The issue is not greenfield.

Historical commits on current lineage already landed:

- `d9ce9057b16077b079a1b0b1b2f1cc49c5e36a1b` — “add transaction pending state before confirmation” (#253): optimistic pending entries keyed by hash and neutral post-submission uncertainty.
- `06809d1f92834434e3a06c5f43f731214ec6b444` — “Add mobile transaction queue view” (#424 / closes #410): History pending queue + manual refresh.

Current source confirms:

- `walletStore.pendingTransactions` is a hash-keyed optimistic map.
- `PendingTransactionQueue` already renders pending items and has a Refresh button.
- History pull-to-refresh calls `refreshWalletData()`.
- `refreshWalletData()` fetches account operations and drops optimistic entries once an operation with matching `transaction_hash` appears.
- `TransactionListItem` understands `tx.status === 'pending'`.

Therefore #322 should **not** add another queue, another generic History refresh, or a second optimistic-pending store.

## Residual defect 1 — status authority is inconsistent

`walletStore.addPendingTransaction(hash, tx)` creates:

```ts
{ ...tx, status: 'pending' }
```

But the transaction detail screen computes:

```ts
const isPending = tx.is_pending === true;
const isFailed = tx.transaction_successful === false;
const isSuccessful = !isPending && !isFailed;
```

and the shared `getTransactionStatus()` helper has the same `is_pending` / `transaction_successful` logic.

The optimistic record added after submission does **not** set `is_pending`; it sets `status: 'pending'`.

Result: a locally pending transaction can appear in History with a **Pending** badge, but the same record can render as **Successful** on its detail screen before Horizon has confirmed it.

This is more serious than a missing refresh button because the user-facing status authority disagrees across surfaces.

### Required repair

Create one canonical status resolver used by list/detail/pending flows. Compatibility-safe precedence should make explicit local status authoritative for optimistic entries while retaining Horizon compatibility, for example:

1. `status === 'pending'` => pending.
2. `status === 'failed'` or `transaction_successful === false` => failed.
3. `status === 'confirmed'` or a confirmed Horizon transaction/operation record => successful/confirmed.
4. `is_pending === true` => pending for legacy/input compatibility.
5. no explicit negative/pending evidence on historical Horizon records => successful/confirmed.

Do not silently treat an “unknown” refresh result as failed.

## Residual defect 2 — transaction hash and operation ID are different identities

The detail route is `/transaction/[id]`.

For an optimistic send, `addPendingTransaction(result.hash, { id: result.hash, ... })` sets the local record **ID to the transaction hash**.

But the deep-link fallback in `app/transaction/[id].tsx` calls:

`fetchOperationById(id)`

and `fetchOperationById()` uses:

`server.operations().operation(operationId).call()`

That endpoint expects a Horizon **operation ID**, not a transaction hash.

A status-refresh implementation must not reuse this lookup for a hash.

### Required repair

Add a distinct transaction-hash API in the Stellar service, e.g.:

```ts
fetchTransactionStatusByHash(hash)
```

using the Horizon transaction endpoint (`server.transactions().transaction(hash).call()` or the exact supported SDK equivalent).

Keep the existing operation-ID lookup for operation deep links. Do not overload one string ID with ambiguous network semantics.

The UI should derive `txHash = tx.hash || tx.transaction_hash` and only expose hash refresh when a non-empty validated transaction hash exists.

## Residual defect 3 — whole-wallet refresh is not a typed status probe

Current History refresh:

1. fetches recent **operations** for the account;
2. matches their `transaction_hash` against optimistic pending keys;
3. drops a pending record when a matching operation appears.

This is useful reconciliation, but it does not provide the transaction-detail action #322 asks for and does not define a typed outcome for an individual hash.

A direct status refresh should produce a narrow result such as:

- `CONFIRMED_SUCCESS`
- `CONFIRMED_FAILED`
- `NOT_YET_VISIBLE` / `UNKNOWN`
- `NETWORK_ERROR`

The exact names are flexible; the semantic separation is not.

### Critical rule: 404 is not “failed”

Horizon can lag immediately after submission, and the current payment code already documents the uncertainty: a client-side timeout can happen after Horizon accepted the transaction.

Therefore:

- transaction lookup 404 immediately after submit => still pending/unknown, not failed;
- transport/timeout => preserve current status and show refresh failure guidance;
- Horizon transaction record with `successful: true` => confirmed;
- Horizon transaction record with `successful: false` => failed;
- only definitive ledger evidence may transition optimistic pending to confirmed/failed.

## Residual defect 4 — current submit error path can lose the best recovery identity

`sendXlmTransaction()` catches the raw SDK/Horizon error and throws a new Error containing only a result code or “Transaction failed”.

`review-transaction.tsx` correctly uses neutral `UNCONFIRMED_SUBMISSION_MESSAGE` for ambiguous errors, but a thrown request can leave the UI without a hash even though the signed transaction hash was deterministically knowable before submission.

This is adjacent to #322 and should be handled deliberately, not accidentally.

### Safe scope

For #322, the refresh button itself should be **available only when a hash exists**, matching the issue acceptance criterion.

If the assigned implementation chooses to improve ambiguous-submit recovery, preserve the signed transaction hash before network submission and return/throw a typed submission outcome that can retain that hash without leaking secrets or encouraging duplicate resubmission. Do not broaden #322 into a transaction-submission rewrite unless the maintainer agrees.

## Assignment-time implementation contract

### 1. Service layer

Add a typed transaction-hash status lookup.

Requirements:

- accepts a transaction hash only;
- calls Horizon transaction lookup, not operation lookup;
- distinguishes 404/not-yet-visible from transport failure;
- returns canonical status data without changing store state itself;
- does not retry submission;
- does not log wallet secrets, signed XDR, or private material.

### 2. Store reconciliation

Add one hash-keyed status update action, e.g. `refreshTransactionStatus(hash)` or a pure reducer + service orchestration.

Requirements:

- idempotent for repeated refresh;
- concurrent double taps do not race contradictory state;
- confirmed success removes/reconciles the hash from `pendingTransactions` and updates the displayed record without duplicating it;
- confirmed failure removes it from pending and leaves a failed record;
- not-yet-visible preserves pending;
- network error preserves the prior record and surfaces recoverable UI state;
- a stale refresh response must not overwrite a newer terminal state.

Prefer updating by canonical transaction hash, not array position.

### 3. Shared status authority

Unify `status`, `is_pending`, and `transaction_successful` into one resolver consumed by:

- `TransactionListItem`
- transaction detail
- filters/helpers as applicable
- pending queue/item

The same object must not be “Pending” in History and “Successful” in Detail.

### 4. Transaction detail action

On `app/transaction/[id].tsx`:

- show Refresh status only for pending/unknown records **and** only when `txHash` exists;
- disable while request is in flight;
- expose an accessible busy state;
- keep the existing detail content visible while refreshing;
- on transient error, show actionable inline/error feedback without relabeling the transaction failed;
- on definitive confirmation/failure, update status in place;
- no refresh action for a record lacking transaction hash.

Do not use `fetchOperationById(txHash)`.

### 5. Existing History queue

Reuse, do not duplicate, `PendingTransactionQueue`.

Whole-wallet refresh may remain as bulk reconciliation. The direct hash action is complementary: it answers the status of the exact detail record.

## Hostile / regression test matrix

At minimum:

1. optimistic record with `status:'pending'`, no `is_pending` => Detail renders Pending, not Successful;
2. same record renders same status in History and Detail;
3. pending + valid hash => Refresh action visible;
4. pending + no hash => Refresh action absent/disabled;
5. confirmed historical operation with no local `status` => remains confirmed;
6. transaction hash lookup calls transaction endpoint, never operation endpoint;
7. lookup 404 => pending/unknown preserved;
8. transport timeout => prior pending state preserved + recoverable failure UI;
9. definitive Horizon `successful:true` => terminal confirmed;
10. definitive Horizon `successful:false` => terminal failed;
11. successful reconciliation removes hash from pending map and does not create duplicate row;
12. failed reconciliation removes hash from pending map but keeps one failed row;
13. repeated refresh after terminal state => idempotent;
14. two rapid refresh taps => one in-flight request or stale-response suppression;
15. terminal success followed by an older pending/404 response => terminal state not downgraded;
16. operation-ID deep-link path still uses operation lookup;
17. hash-status path does not accept an empty hash;
18. refresh does not resubmit the transaction;
19. refresh/load/error controls expose accessibility role/label/busy state;
20. existing History `PendingTransactionQueue` + pull-to-refresh behavior remains intact.

## Suggested focused files after assignment

Likely smallest coherent patch:

- `src/services/stellar.ts`
- `src/store/walletStore.ts`
- `src/features/transactions/helpers.ts` / types if a typed resolver/result is added
- `app/transaction/[id].tsx`
- focused service/store/detail tests
- transaction feature README or SDK-assumption note

Avoid touching unrelated payment construction, vault, signer, or pagination logic.

## Verification

Use repository-native checks once implementation exists:

```bash
npm test -- --runInBand
npm run typecheck
npm run lint
npm run api:check
```

If the repo’s current baseline has unrelated failures, record exact commands/exit codes and prove the focused suite independently; do not relabel a partial pass as whole-repo green.

## Next authority gate

Provider/maintainer assignment is still required before upstream implementation. Re-check issue comments, assignee, open PRs, current default-branch SHA, and provider state immediately before coding because this campaign is moving quickly.
