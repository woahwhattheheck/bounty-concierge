# Collection dispatch guard

`concierge.collection_dispatch_guard` closes the race between producing a valid
sponsor collection request and actually touching an outbound provider.

The repository already has three independent pieces of authority:

- `collection_request` proves what assessment/payment request may be made and
  binds it to merged work, advertised terms, payout route, and (when present)
  sponsor acceptance/award evidence.
- `outbound_dedupe` consumes a trusted inventory of every send-capable provider
  plus fresh provider-side search receipts and refuses a send when any matching
  sent message exists or the census is incomplete/stale.
- `outbound_singlewriter` elects one writer from a complete durable coordination
  snapshot and supplies a local crash-aware lease state machine.

Before this guard those authorities could be called independently. Two seats
could therefore both hold valid collection packets and race across different
providers before either primary dispatch reached `collection_custody`.

## Invariant

For one normalized `sponsor + GitHub repo + PR + collection phase`, there is
exactly one provider-neutral operation identity.

The identity deliberately ignores:

- selected email/provider;
- exact recipient address;
- payout route;
- regenerated collection packet receipt; and
- sponsor-name casing / Unicode-width presentation.

That means changing Gmail to another provider, using another contact address, or
regenerating a packet cannot manufacture another writer. Repository identity is
case-folded because GitHub repository names are case-insensitive. Sponsor identity
is NFKC-normalized and case-folded, so case/width presentation changes do not
mint another writer. Distinct sponsor names intentionally receive distinct
identities so legitimate multi-sponsor / stacked-prize collection is not blocked.

Assessment and payment are different phases. A merged contribution can therefore
have one assessment request and, after real acceptance/award evidence exists, one
payment request per sponsor without weakening duplicate protection.

## Required pre-send sequence

1. Compile the request with `collection_request`.
2. Post/acknowledge one shared `CLAIM` for the derived `operation_key`.
3. Collect a **complete fresh census** from every trusted send-capable provider
   for the exact recipient and derived `offer_key`.
4. Read a complete post-ack shared-claim snapshot.
5. Call `authorize_collection_dispatch(...)`.
6. Only when the result is `CLEAR` and `dispatch=true`, call
   `acquire_guarded_local_lease(...)` with the same elected owner.
7. The external sender may proceed only while it owns that local lease/permit,
   then it must record the provider receipt using the existing
   `outbound_singlewriter` and `collection_custody` lifecycle.

A guard receipt never sends anything itself.

## Fail-closed outcomes

`DNR` is terminal for the attempted operation when either provider truth already
contains a matching sent message or the shared coordination ledger already has a
terminal `SENT` event.

`HOLD` means there is no send authority. Examples include a missing/incomplete/
stale provider query, incomplete shared snapshot, losing the shared election,
missing own acknowledged claim, or malformed shared evidence.

Malformed/tampered collection packets and tampered guard receipts raise
`CollectionDispatchGuardError` rather than degrading to permission.

## CLI

Authorization can be evaluated offline:

```bash
python -m concierge.collection_dispatch_guard evidence.json \
  --required-provider gmail \
  --required-provider outlook
```

The JSON input must contain exactly:

```json
{
  "packet": {},
  "recipient": "payables@example.com",
  "provider_queries": [],
  "owner": "seat-id",
  "claim_event_id": "acknowledged-claim-id",
  "shared_events": [],
  "snapshot_complete": true
}
```

Exit codes: `0` = CLEAR, `2` = HOLD/input error, `3` = DNR.

## Authority ceiling

This module does **not** send email/comments, mutate providers, submit claims,
initiate payouts, touch wallets, recognize revenue, or turn an advertised reward
into debt/cash. It only composes already-existing evidence gates and emits a
receipt proving whether one elected writer may advance to the local pre-send
lease.
