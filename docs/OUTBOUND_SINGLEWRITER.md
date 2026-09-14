# Outbound single-writer guard

Revenue outreach must not become a race between agents.  This control gives an
outbound mutation one stable SHA-256 operation key, one durable shared claimant,
and one local send permit.

It does **not** send email, Slack messages, GitHub comments, payment requests, or
customer messages.  It exists to decide who is allowed to do that external action.

> **Authority upgrade:** indexed Slack/search snapshots are not a safe mutex across
> cloud sessions because read-after-write visibility can lag. For cross-session
> revenue sends, prefer `GitHubRefClaimStore` from `concierge.outbound_gitref` and
> `docs/OUTBOUND_GITREF_CAS.md`: atomic ref creation plus non-force fast-forward CAS
> is the cooperative-writer authorization boundary. Repository admins can defeat
> it unless the claim namespace is protected against deletion/force rewrites. The shared-snapshot election below is suitable
> only when its provider actually guarantees a complete linearizable snapshot;
> otherwise use it as audit/diagnostics, not permission to send.

## Why there are two layers

A filesystem lock is not a swarm lock.  Separate cloud sessions do not share a
local disk, so local mutual exclusion alone cannot stop two seats from emailing the
same lead seconds apart.

The protocol therefore uses both:

1. **Shared claim election.** Every seat derives the same operation key from
   `(provider, canonical destination, stable thread/lead id, stable operation)`.
   It posts a durable `CLAIM` containing that key to the shared coordination feed.
   Only after the provider acknowledges the claim does the seat run a fresh **exact
   key** search.  Search failure, rate limiting, incomplete pagination, or absence
   of the seat's own acknowledged claim means **do not send**.  The earliest active
   claim wins.  Claims never expire automatically; they end only with explicit
   `RELEASE` or terminal `SENT` evidence.
2. **Local send state machine.** The winning seat acquires a short HELD lease in
   `OutboundSingleWriter`.  The lease can expire/recover while no external send is
   possible.  Immediately before the external provider mutation the winner calls
   `prepare_send()`, entering non-expiring `SENDING`.  A crash in that state blocks
   all retries until the provider is reconciled.  Success is finalized with the
   provider's receipt id and becomes permanently `SENT`.

This split closes both common races: two workers on one host and two independent
cloud sessions using the same coordination authority.

## Stable identity

Use the same values across every peer:

- `provider`: e.g. `gmail`, `slack`, `github`;
- `destination`: the canonical recipient/channel/repository destination;
- `thread`: an existing provider thread id, or a stable lead/customer id for a new
  thread (never a per-seat random UUID);
- `operation`: stable business action, e.g. `initial-outreach`,
  `merged-bounty-collection`, or `proposal-followup-1`.

`outbound_operation_key()` hashes the normalized identity, so the shared feed can
coordinate on the exact key without publishing the destination itself.

## Fallback shared-snapshot protocol (linearizable stores only)

Assume the derived key is `K` and this seat is `Z-MengerKiln-...`.

1. Search the shared coordination provider for exact `K`.  If a terminal `SENT`
   exists, stop.  If another active claim exists, stop or obtain an explicit
   release/handoff.
2. Post `OUTBOUND CLAIM · key=K · owner=<seat>` and retain the provider event id / 
   monotonic order (Slack `message_ts` works).
3. **After the claim post is acknowledged**, perform a second exact-key search.
   Fetch all pages.  A 429, auth error, partial page, ambiguous parse, or missing
   self-claim is a hard block.
4. Map the durable messages to `CLAIM` / `RELEASE` / `SENT` events and run
   `evaluate_shared_claim(..., snapshot_complete=True)`.  Only
   `disposition=AUTHORIZED` may continue.
5. Acquire the local lease and do any last non-mutating provider read.
6. Call `prepare_send()` immediately before the provider send.  Then perform
   exactly one external mutation.
7. On a provider success receipt, call `finalize_sent()` and post one durable
   `OUTBOUND SENT · key=K · owner=<seat> · receipt=<safe receipt id>` event.
8. If the process enters `SENDING` but no receipt is recorded, **do not retry**.
   Search the provider authoritatively.  Only if evidence proves the send did not
   happen may `reconcile_not_sent()` release the operation; record the evidence
   reference.  Uncertainty remains blocked.

A winner that decides not to send may call `abort_held()` and post `RELEASE` only
while still HELD.  There is deliberately no timeout-based release of shared claims
or SENDING permits.

## CLI and local API

The module is directly executable for stable-key calculation, shared election,
and local-state inspection without adding a console-script dependency:

```bash
python -m concierge.outbound_singlewriter key \
  --provider gmail \
  --destination buyer@example.com \
  --thread lead:buyer-123 \
  --operation initial-outreach
```

For shared election, save the complete exact-key provider snapshot as normalized
JSON events and run:

```bash
python -m concierge.outbound_singlewriter elect \
  --key "$K" \
  --owner Z-MengerKiln-2216-T2F9 \
  --claim-event-id 1789352960.235499 \
  --events-json /tmp/outbound-events.json \
  --snapshot-complete
```

Exit code `0` means authorized. A non-winner exits `3`; guard/schema errors exit
`2`. The local mutation lifecycle is deliberately an explicit Python API so the
provider sender has to carry the exact returned lease/permit rather than scraping
CLI text:

```python
guard = OutboundSingleWriter("/secure/local/state")
held = guard.acquire(
    provider="gmail", destination="buyer@example.com",
    thread="lead:buyer-123", operation="initial-outreach",
    owner="Z-MengerKiln-2216-T2F9", ttl_seconds=300,
)
permit = guard.prepare_send(
    operation_key=held["operation_key"],
    lease_id=held["lease_id"], owner=held["owner"],
)
# Perform exactly one provider mutation here.
guard.finalize_sent(
    operation_key=held["operation_key"], permit_id=permit["permit_id"],
    owner=held["owner"], provider_receipt_id="provider-message-id",
)
```

## Safety boundaries

- This is idempotency and custody evidence, not proof a customer owes money or a
  sponsor accepted a claim.
- Never put credentials, message bodies, API tokens, private data, or wallet keys in
  operation fields, evidence references, or coordination events.
- Destination normalization is intentionally conservative.  Callers must agree on
  one canonical destination string; the guard will not guess that two aliases are
  equivalent.
- Local state integrity is SHA-256 sealed and writes are `fsync` + atomic replace.
  Corrupt or malformed state fails closed.
- `SENDING` does not auto-expire.  Time passing is not evidence that an external
  mutation failed.
- Shared claim election has no auto-TTL.  A stale owner must explicitly release or
  be reconciled/handoffed from durable authority before another sender proceeds.
