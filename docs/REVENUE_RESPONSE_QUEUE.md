# Revenue Response Queue

`concierge.revenue_response_queue` is the read-only control plane between an already-sent revenue message and engagement closeout. It classifies authenticated provider evidence into an operator queue without sending mail, replying to buyers, mutating a provider, changing contact policy, or recognizing payment/revenue.

The v3 design has two independent trust fences:

1. **Provider custody:** caller-built callbacks/snapshots cannot claim to be provider truth. A credential-owning host must HMAC-attest a complete provider-read batch.
2. **Provider authorization:** a valid host signature is not enough by itself. The signed batch's provider and authenticated principal must also exact-match independently configured host authorization.

Thread membership is not commercial-engagement authority. A later message advances or satisfies an engagement only when provider-normalized `related_message_id` proves a continuation of the current generation.

## Public API

```python
compile_revenue_response_queue(
    manifest,
    provider_read_batch,
    *,
    policy,
)
```

There is no public fetch callback and no caller-selected `now`.

The public verifier is:

```python
verify_revenue_response_queue_receipt(receipt)
```

It accepts no caller-selected key, provider identity, principal identity, or current time.

## Host configuration

The credential-owning/verifying host supplies four values outside request data:

```text
BOUNTY_RESPONSE_QUEUE_ATTESTATION_KEY_B64
BOUNTY_RESPONSE_QUEUE_ATTESTATION_KEY_ID
BOUNTY_RESPONSE_QUEUE_AUTHORIZED_PROVIDER
BOUNTY_RESPONSE_QUEUE_AUTHORIZED_PRINCIPAL_SHA256
```

The attestation key must decode to at least 32 bytes. `AUTHORIZED_PRINCIPAL_SHA256` is the lowercase SHA-256 hex digest of the exact authenticated-principal identifier exposed by the trusted provider adapter.

A batch signed under the correct HMAC key is rejected if its provider or principal does not match those independent host bindings. This prevents a correctly signed batch from another account/provider from being transplanted into the same manifest/policy/thread set.

The library deliberately does not expose a production signing helper. Provider acquisition and signing belong inside the credential-owning host boundary. Webhooks, request payloads, CRM rows, screenshots, model output, and arbitrary callbacks are data, not provider-read authority.

## Trusted policy

Policy is an exact host/operator object:

```json
{
  "schema": "bounty-concierge.revenue-response-policy/v1",
  "max_snapshot_age_seconds": 300,
  "auto_ack_grace_hours": 24
}
```

The signed batch commits `policy_sha256`, so provider evidence cannot be replayed under a different freshness or auto-ack policy.

## Operator manifest

Each engagement binds:

- `engagement_id`
- `provider_thread_id`
- `sent_message_id`
- `buyer_route`
- `authorized_reply_routes`
- `offer_key`
- `contact_policy`
- `follow_up_after_hours`

Duplicate engagement, thread, sent-message, and `(buyer_route, offer_key)` bindings fail closed.

The canonical manifest is committed as `scope_sha256`. Each item additionally commits the complete manifest row, buyer route, and complete authorized-route set without exposing raw addresses.

## Provider-read batch

After all authenticated provider reads finish, the trusted host emits one exact batch:

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

`hmac_sha256` covers canonical JSON of every other field. Before any semantic use, the compiler canonicalizes the exact caller batch once, reconstructs fresh built-in JSON containers from that retained serialization, and verifies and consumes only that detached generation. Later mutation of the caller-owned Python graph therefore cannot inject evidence after HMAC verification. The compiler then verifies exact scope/policy digests, host-authorized provider/principal, capture time, and an exact one-snapshot-per-manifest-thread set before classification.

Verifier-owned UTC is sampled only after the completed/attested provider acquisition. This avoids treating an honest provider timestamp created during the read as future evidence.

## Normalized snapshot

Each provider snapshot is exact, complete, fresh, and body-free:

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

Allowed kinds are `outbound`, `human_inbound`, `automated_inbound`, and `bounce`.

The provider normalizer owns two important evidence fields:

- `sequence`: a stable integer ordinal used when timestamps tie. Distinct messages cannot share a sequence.
- `related_message_id`: the provider-grounded continuation/reply relation. It is the only evidence allowed to advance a commercial generation or attribute an inbound event to the current generation.

Subjects, bodies, and arbitrary headers never enter the authority envelope.

## Engagement-generation semantics

The retained `sent_message_id` is the initial generation anchor. It must exist in the authenticated snapshot, be provider-classified `outbound`, and target the bound buyer.

A later outbound advances the generation only when all are true:

1. it occurs after the current baseline;
2. it targets an authorized route; and
3. `related_message_id` equals the current baseline message id.

A unique linked successor becomes the new baseline and resets the follow-up clock. This can repeat as a chain.

A later same-thread outbound to the same buyer **without** that relation does not reset the offer clock. It surfaces `HUMAN_REVIEW_REQUIRED`. Multiple direct linked successor branches are also review-required rather than guessed.

The same rule applies to inbound evidence:

- an authorized human inbound becomes `HUMAN_REPLY` only when it is linked to the current baseline;
- a linked human from an unbound route becomes `HUMAN_REVIEW_REQUIRED`;
- an authorized but unlinked human/auto inbound becomes `HUMAN_REVIEW_REQUIRED` instead of being silently attributed to the offer;
- a bounce must link to the current baseline to become `ROUTE_REPAIR`;
- only a linked automated acknowledgement may extend the follow-up grace window.

Provider order also governs conflicts between otherwise decisive evidence and ambiguity. The classifier first selects the same linked positive/bounce decision it would have made without ambiguity, then compares it with the newest ambiguous same-thread event. If ambiguity is newer, it overrides the stale positive/bounce state and emits `HUMAN_REVIEW_REQUIRED`. If the ambiguity is older and a later correctly linked human reply (or other otherwise-selected linked decision) arrives, the later linked evidence may remain authoritative. This prevents an older reply from masking a newer unlinked outbound/inbound while avoiding a permanent review lock after ambiguity has been resolved by newer provider-grounded evidence.

This prevents activity for a second offer in a long-lived buyer thread from consuming or delaying the first offer.

## Queue states

Priority order is:

1. `HUMAN_REPLY`
2. `HUMAN_REVIEW_REQUIRED`
3. `ROUTE_REPAIR`
4. `FOLLOW_UP_DUE`
5. `WAIT_AUTO_ACK`
6. `WAIT_BUYER_EVENT`
7. `WAIT`

This priority controls queue sorting; it does **not** let an older high-priority classification outrank newer provider evidence inside one engagement generation.

`wait_for_buyer_event` requires `follow_up_after_hours: null`, so time alone cannot convert a one-touch/DNR motion into a follow-up recommendation.

## Durable receipt

The output exposes no raw buyer address, representative address, provider name, provider principal, provider thread id, or provider message id. It contains SHA-256 commitments for audit/collision purposes.

Two integrity fields serve different roles:

- `evidence_sha256`: unkeyed content checksum over the safe semantic receipt;
- `host_attestation_hmac_sha256`: host-authenticity proof over the receipt.

The HMAC-bound `provider_authority` also retains `batch_captured_at` and `oldest_snapshot_fetched_at`. `verify_revenue_response_queue_receipt()` rechecks the host HMAC, content checksum, policy digest, configured key id, independently authorized provider/principal hashes, verifier-owned UTC, and the active freshness window against the receipt time, attested batch capture, and oldest underlying provider snapshot. Minting a newer receipt therefore cannot extend the lifetime of older provider evidence. Recomputing plain SHA-256 after tampering is insufficient.

Every successful receipt explicitly grants only evidence authority:

```json
{
  "provider_read_attested": true,
  "provider_identity_authorized": true,
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

Any later customer-contact path must independently enforce identity, authorization, outbound dedupe, contact policy, and provider-write controls.

## Fail-closed conditions

Compilation refuses to emit an authoritative queue when, among other things:

- the host key or independent authorized provider/principal binding is unavailable;
- HMAC, key id, provider, principal, scope, or policy binding fails;
- the batch/snapshot is stale, future-dated, incomplete, or thread-mismatched;
- the retained anchor is missing, wrong-kind, or does not target the buyer;
- provider ids/sequences conflict;
- schemas contain unexpected fields;
- manifest collision fences fail.

Ambiguous same-thread commercial activity does not fail the entire queue; it is represented as `HUMAN_REVIEW_REQUIRED` while retaining the last proven generation baseline. When positive/bounce and ambiguous evidence coexist, provider order determines whether the ambiguity is still active; newer ambiguity fails the engagement closed to review.

## Validation

The hostile suite runs normally and under `python -O` and covers:

- arbitrary callback rejection;
- missing/wrong host key;
- correctly HMAC-signed wrong-provider and wrong-principal transplant rejection;
- scope/policy replay;
- buyer/authorized-route receipt binding;
- post-read clock/freshness and exact thread sets;
- linked vs unlinked human attribution;
- linked human followed by newer unlinked outbound/inbound ambiguity failing to `HUMAN_REVIEW_REQUIRED`;
- linked bounce followed by newer ambiguity failing to `HUMAN_REVIEW_REQUIRED`;
- older ambiguity followed by a newer correctly linked human resolving to `HUMAN_REPLY`;
- bounce binding;
- unrelated same-thread outbound not resetting the offer clock;
- positively linked successor outbound advancing the generation;
- ambiguous linked successor branches surfacing review;
- DNR and linked auto-ack semantics;
- sequence/anchor/strict-schema fences;
- identity-minimized output and explicit action ceiling;
- post-HMAC mutation of the caller-owned batch cannot alter classification;
- receipt freshness expires with the underlying provider snapshot even while the receipt itself is still young;
- receipt HMAC, identity authorization, tamper, and stale-replay verification;
- deterministic input order and manifest collision rejection.

Repository GitHub Actions remains the authoritative uploaded-byte execution gate before merge.