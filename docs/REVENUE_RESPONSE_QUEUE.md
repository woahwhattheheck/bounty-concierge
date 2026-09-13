# Revenue Response Queue

`concierge.revenue_response_queue` is a read-only decision-support layer for the
period after an outbound revenue message is sent and before an engagement is
closed or settled.

It exists to answer one narrow question from provider truth:

> Which already-sent engagements need a human response, route repair, follow-up,
> or continued waiting right now?

It does **not** send messages, reply to buyers, mutate a mailbox, alter contact
policy, recognize revenue, or recognize payment.

## Why this exists

The revenue stack already has separate controls for intake, dispatch, outbound
dedupe, closeout, settlement, and portfolio allocation. Without a provider-
grounded post-send queue, operators can still lose money in the middle:

- a real human reply can be buried below auto-acknowledgements;
- a bounced route can remain mistaken for a live sales path;
- an already-sent follow-up can be forgotten and duplicated;
- a one-touch / DNR engagement can be accidentally converted into an automated
  follow-up campaign;
- stale screenshots or caller-built CRM rows can be mistaken for current inbox
  truth.

This module makes that middle state explicit and fail-closed.

## Authority model

The production API is:

```python
compile_revenue_response_queue(
    manifest,
    fetch_authenticated_thread,
    *,
    max_snapshot_age_seconds=300,
    auto_ack_grace_hours=24,
)
```

There are two authority classes.

### 1. Operator-owned manifest scope

The manifest says which engagements are allowed to participate and binds:

- `engagement_id`
- `provider_thread_id`
- `sent_message_id`
- `buyer_route`
- `authorized_reply_routes`
- `offer_key`
- `contact_policy`
- `follow_up_after_hours`

This is policy/scope, not proof that a reply, bounce, or send actually happened.

### 2. Trusted provider reacquisition capability

`fetch_authenticated_thread(thread_id)` is a **trusted host capability**. A
production host must keep this callback inside the credential-owning provider
adapter and perform a fresh authenticated provider read for the exact retained
thread id.

Do not implement it as a lambda over:

- webhook payloads;
- caller-supplied message objects;
- cached CRM rows;
- screenshots;
- previously serialized queue input.

Those are data, not independent reacquisition authority.

The core calls the callback exactly once per bound thread.

Current time is verifier-owned: the public API does not accept a caller-selected
`now`. Freshness and follow-up thresholds use the module's current UTC clock,
so a request cannot manufacture or suppress a due action by choosing time.

## Normalized provider snapshot

The trusted adapter returns an exact object:

```json
{
  "thread_id": "provider-thread-id",
  "fetched_at": "2026-09-13T12:34:56Z",
  "complete": true,
  "messages": [
    {
      "id": "provider-message-id",
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

Supported `kind` values are:

- `outbound`
- `human_inbound`
- `automated_inbound`
- `bounce`

The adapter, not this core, is responsible for mapping provider-native records
to those kinds. In particular, `human_inbound` versus `automated_inbound` must
come from authenticated provider metadata / a trusted normalization policy; do
not infer it from arbitrary caller prose.

`sequence` is a provider-normalizer-owned stable integer ordinal. It disambiguates
multiple messages with the same timestamp. Distinct messages may not share a
sequence. This prevents lexical message ids from silently becoming chronology.

The snapshot must be explicitly complete and fresh. Messages after
`snapshot.fetched_at`, conflicting reuse of a provider message id, malformed
routes, unknown kinds, or stale snapshots fail the entire compilation.

## Anchor and generation semantics

The retained `sent_message_id` must:

1. appear in the freshly reacquired exact thread;
2. be provider-classified as `outbound`; and
3. target the bound `buyer_route`.

The queue then finds the **latest provider-observed outbound** at or after that
anchor that targets an authorized buyer route.

That latest outbound becomes the generation boundary.

This matters because a human reply may already have been consumed by a later
operator reply. Example:

1. seller sends `sent-1`;
2. buyer replies;
3. seller sends `sent-2`.

The old buyer reply is not re-enqueued. `sent-2` becomes the current baseline
and its time resets the follow-up clock.

## States

Items are sorted by action priority.

### `HUMAN_REPLY`

A provider-classified human inbound arrived after the latest qualifying outbound
from one of the exact `authorized_reply_routes`.

This is the highest-priority normal state.

### `HUMAN_REVIEW_REQUIRED`

A provider-classified human inbound arrived after the latest outbound, but its
sender is not in `authorized_reply_routes`.

The core refuses to silently equate that sender with the buyer. It still
surfaces the event so a new representative or forwarded conversation is not
lost.

### `ROUTE_REPAIR`

A provider-classified bounce is explicitly bound to the latest outbound message
id.

This does not authorize contacting another route. It says the retained route
needs operator repair.

### `FOLLOW_UP_DUE`

`contact_policy == "follow_up_allowed"` and the latest outbound has crossed its
configured threshold.

If a trusted automated acknowledgement arrived from an authorized route, the
next follow-up time is the later of:

- latest outbound + `follow_up_after_hours`; or
- latest automated acknowledgement + `auto_ack_grace_hours`.

### `WAIT_AUTO_ACK`

An authorized automated acknowledgement exists and its grace window is still
active.

An auto-ack is **not** treated as a human buyer event.

### `WAIT_BUYER_EVENT`

`contact_policy == "wait_for_buyer_event"` and there is no actionable human
reply or bounce.

This is the representation for one-touch / DNR-until-buyer-event sales motions.
`follow_up_after_hours` must be `null` for this policy, so elapsed time cannot
silently create a follow-up recommendation.

A later authenticated human reply can move the engagement to `HUMAN_REPLY`.

### `WAIT`

A follow-up-capable engagement exists, but its provider-grounded latest outbound
has not crossed the threshold.

## Collision fences

The manifest rejects duplicate:

- engagement ids;
- provider thread ids;
- sent message ids;
- `(buyer_route, offer_key)` active engagement pairs.

Those fences prevent two active queue rows from independently recommending work
against the same buyer/offer identity.

The module also re-baselines on newer provider-observed outbound messages, so a
follow-up that actually happened is not forgotten merely because the manifest
still points at the original anchor.

## Privacy / output minimization

The provider envelope intentionally has no subject or body field. Extra fields
are rejected.

Queue output contains no raw:

- email addresses;
- provider thread ids;
- provider message ids;
- subjects;
- bodies;
- headers.

Routes and provider ids enter only SHA-256 evidence projections. `engagement_id`
and `offer_key` are retained as operator-owned stable keys.

The output includes a deterministic `evidence_sha256` over the complete safe
decision receipt, including capture time and policy parameters.

## Explicit authority ceiling

Every receipt includes:

```json
{
  "authority": {
    "send_message": false,
    "reply_to_buyer": false,
    "mutate_provider": false,
    "change_contact_policy": false,
    "recognize_revenue": false,
    "recognize_payment": false
  }
}
```

The queue is prioritization evidence only. A later action path must run its own
outbound dedupe, policy, identity, authorization, and provider-write controls.

## Example manifest

```json
[
  {
    "engagement_id": "cellares-cross-factory-qc",
    "provider_thread_id": "provider-thread-opaque",
    "sent_message_id": "provider-sent-opaque",
    "buyer_route": "buyer@example.com",
    "authorized_reply_routes": [
      "buyer@example.com",
      "representative@example.com"
    ],
    "offer_key": "cross-factory-qc-v1",
    "contact_policy": "wait_for_buyer_event",
    "follow_up_after_hours": null
  }
]
```

For a conventional follow-up motion:

```json
{
  "contact_policy": "follow_up_allowed",
  "follow_up_after_hours": 72
}
```

## Fail-closed conditions

Compilation aborts instead of emitting a partial green queue when:

- the provider callback is unavailable or raises;
- a snapshot is stale, future-dated, incomplete, or bound to another thread;
- the retained sent id is missing or is not the expected outbound;
- the bound outbound did not actually target the buyer route;
- a provider message occurs after `fetched_at`;
- distinct provider messages reuse an id or stable sequence;
- schemas contain unexpected fields;
- contact/follow-up policy is malformed;
- active buyer/offer or provider bindings collide.

A partial queue would be dangerous because omitted provider evidence can make a
follow-up look safe. Whole-compilation failure keeps unknown authority from
becoming action.

## Validation

The hostile suite exercises:

- human reply priority;
- unbound-human review;
- newer outbound generation reset;
- latest-outbound bounce binding;
- no-reply follow-up thresholds;
- automated-ack grace;
- one-touch / DNR semantics;
- exact retained message and recipient binding;
- complete/fresh snapshot requirements and verifier-owned current time;
- provider callback failure;
- conflicting/duplicate provider evidence;
- stable same-time provider ordering;
- collision rejection;
- strict schemas;
- privacy-safe output;
- deterministic receipts;
- optimized (`python -O`) execution.

The repository-wide GitHub Actions rail compiles the entire package and runs all
tests on Python 3.9 and Python 3.13 before merge.
