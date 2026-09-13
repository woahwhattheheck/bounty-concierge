# Revenue Response Queue

`concierge.revenue_response_queue` is the read-only control plane between an already-sent revenue message and engagement closeout. It classifies provider-observed post-send state into a deterministic operator queue without sending mail, replying to buyers, mutating a provider, changing contact policy, or recognizing payment/revenue.

The v2 design is intentionally stricter than a normal callback interface: **caller data cannot claim to be provider truth.**

## Authority boundary

The authoritative API is:

```python
compile_revenue_response_queue(
    manifest,
    provider_read_batch,
    *,
    policy,
)
```

There is no public fetch callback and no caller-selected `now`.

A credential-owning host must first reacquire every exact provider thread. After those reads finish, the host builds one complete batch and HMAC-attests it with a host-only secret. The batch binds:

- the canonical operator manifest (`scope_sha256`);
- the canonical host/operator queue policy (`policy_sha256`);
- provider identity and authenticated principal;
- batch capture time;
- the exact normalized provider snapshots.

The compiler verifies that attestation before the snapshots can mint `HUMAN_REPLY`, `FOLLOW_UP_DUE`, or any other authoritative queue state.

### Host configuration

The verifying host supplies:

```text
BOUNTY_RESPONSE_QUEUE_ATTESTATION_KEY_B64
BOUNTY_RESPONSE_QUEUE_ATTESTATION_KEY_ID
```

The HMAC key must decode to at least 32 bytes. The library deliberately does **not** ship a production signing helper. The provider adapter that owns credentials must own signing too; request payloads, webhooks, CRM rows, model output, and arbitrary caller callbacks are not allowed to act as the signer.

Threat model: the attestation secret must not be readable or writable by untrusted request data. Code already executing with arbitrary access to the credential-owning host process/environment is inside this trust boundary and must be isolated by the host/runtime, not by this pure-Python module.

## Trusted policy

Policy is an exact object:

```json
{
  "schema": "bounty-concierge.revenue-response-policy/v1",
  "max_snapshot_age_seconds": 300,
  "auto_ack_grace_hours": 24
}
```

These are host/operator policy, not buyer/request-controlled knobs. The HMAC-attested batch commits the exact policy digest, so a batch cannot be replayed under a looser freshness window or different auto-ack grace period.

## Operator manifest

Each active engagement binds:

- `engagement_id`
- `provider_thread_id`
- `sent_message_id`
- `buyer_route`
- `authorized_reply_routes`
- `offer_key`
- `contact_policy`
- `follow_up_after_hours`

The compiler rejects duplicate engagement ids, thread ids, sent message ids, and `(buyer_route, offer_key)` pairs.

`contact_policy` is either:

- `follow_up_allowed`, with an integer `follow_up_after_hours`; or
- `wait_for_buyer_event`, with `follow_up_after_hours: null`.

The canonical full manifest is hashed into `scope_sha256`. Each output item also contains privacy-safe commitments to its complete manifest row, exact buyer route, and complete authorized-reply-route set. A buyer or representative substitution therefore changes the receipt even when no current inbound event uses that route.

## Provider-read batch

The credential-owning adapter normalizes its completed read into:

```json
{
  "schema": "bounty-concierge.provider-thread-read-batch/v1",
  "key_id": "host-v1",
  "provider": "gmail",
  "authenticated_principal": "provider-account-opaque",
  "scope_sha256": "...",
  "policy_sha256": "...",
  "captured_at": "2026-09-13T13:10:00.400000Z",
  "snapshots": [],
  "hmac_sha256": "..."
}
```

The HMAC is SHA-256 over canonical JSON of every field except `hmac_sha256`.

The batch must contain exactly one snapshot per manifest thread and no extra thread. `captured_at` is stamped **after provider acquisition**, which lets the compiler sample its own UTC time after the completed reads without falsely rejecting an honest fresh read as future evidence.

## Normalized snapshot contract

Each snapshot is exact and body-free:

```json
{
  "thread_id": "provider-thread-opaque",
  "fetched_at": "2026-09-13T13:10:00.300000Z",
  "complete": true,
  "messages": [
    {
      "id": "provider-message-opaque",
      "kind": "outbound",
      "occurred_at": "2026-09-13T10:00:00Z",
      "from_route": "seller@example.net",
      "to_routes": ["buyer@example.com"],
      "related_message_id": null,
      "sequence": 100
    }
  ]
}
```

Allowed message kinds are `outbound`, `human_inbound`, `automated_inbound`, and `bounce`. Human-vs-automated classification is the authenticated provider adapter's responsibility; the queue does not infer it from subject/body prose.

`sequence` is a provider-normalizer-owned stable integer ordinal. It resolves same-timestamp chronology without inventing order from lexical provider ids. Distinct messages cannot share one sequence.

The compiler fails closed when a snapshot is stale, future-dated, incomplete, bound to the wrong thread, contains a message after its own `fetched_at`, conflicts on provider message identity, or violates the exact schema.

## Generation semantics

The retained `sent_message_id` must exist in the fresh provider snapshot, be classified as `outbound`, and target `buyer_route`.

The queue then finds the newest provider-observed outbound at or after that anchor that targets an authorized reply route. That newest outbound becomes the generation boundary. Earlier replies and bounces are not re-enqueued after a later outbound, and the later outbound resets the follow-up clock.

## States

Priority order is:

1. `HUMAN_REPLY` — human inbound after the latest outbound from an authorized route.
2. `HUMAN_REVIEW_REQUIRED` — human inbound from an unbound route; surfaced without pretending the sender is the buyer.
3. `ROUTE_REPAIR` — a bounce explicitly bound to the latest outbound.
4. `FOLLOW_UP_DUE` — follow-up-capable engagement crossed its host policy threshold.
5. `WAIT_AUTO_ACK` — trusted automated acknowledgement extends the grace window.
6. `WAIT_BUYER_EVENT` — one-touch/DNR policy remains in force until a real buyer event.
7. `WAIT` — follow-up is allowed, but not due yet.

An auto-ack never becomes a human buyer event. A DNR engagement cannot become follow-up-due merely because time elapsed.

## Durable receipt authenticity

The compiler emits two integrity fields with different jobs:

- `evidence_sha256` is an unkeyed content checksum over the safe semantic receipt.
- `host_attestation_hmac_sha256` is the trust root proving the durable receipt was emitted under the host attestation key.

A caller can recompute SHA-256 after tampering, so `evidence_sha256` alone is **not** authority. Downstream code should require:

```python
verify_revenue_response_queue_receipt(receipt)
```

The verifier checks the host HMAC, evidence checksum, policy digest, configured key id, verifier-owned current UTC, and receipt freshness. It accepts no caller-selected key and no caller-selected current time.

Provider name, authenticated principal, routes, thread ids, and message ids are represented only by SHA-256 commitments in durable output. Subject, body, and arbitrary headers never enter the provider envelope.

## Authority ceiling

Every successful receipt states that provider read, scope, policy, and receipt are attested, while explicitly denying action authority:

```json
{
  "provider_read_attested": true,
  "scope_attested": true,
  "policy_attested": true,
  "receipt_host_attested": true,
  "send_message": false,
  "reply_to_buyer": false,
  "mutate_provider": false,
  "change_contact_policy": false,
  "recognize_revenue": false,
  "recognize_payment": false
}
```

This module prioritizes already-existing evidence. Any later send/reply path must independently enforce identity, authorization, outbound dedupe, contact policy, and provider-write controls.

## Validation

The hostile suite covers the authority boundary and core decision semantics under normal and optimized Python execution, including:

- arbitrary callback rejection;
- missing/wrong host key and invalid HMAC;
- manifest-scope and policy replay rejection;
- buyer/representative substitution changing the durable receipt;
- post-read clock ordering and batch/snapshot freshness;
- exact thread-set and complete-snapshot requirements;
- human/unbound-human/bounce/follow-up/auto-ack/DNR states;
- latest-outbound generation reset;
- anchor recipient/type and stable-sequence fences;
- identity-minimized output and explicit action ceiling;
- host-HMAC receipt verification;
- rejection of a forged receipt even after an attacker recomputes plain SHA-256;
- stale receipt replay rejection;
- strict manifest/policy/batch shapes and collision fences.

Repository GitHub Actions remains the authoritative uploaded-byte gate before merge.
