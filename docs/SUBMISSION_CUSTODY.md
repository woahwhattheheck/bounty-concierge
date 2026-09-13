# Submission custody and sponsor-response ledger

`concierge.submission_custody` is the offline custody layer between a finished `bounty-submission-packet/v1` and the existing closeout / wallet-settlement rails.

It solves a specific fleet failure mode: several workers can independently rediscover the same ready artifact and re-send it through GitHub, email fallback, or another sponsor route because there is no canonical record of **which exact artifact revision was already submitted and what the sponsor did next**.

This layer does **not** send anything. It records evidence supplied by an operator after an external action or sponsor response has happened.

## Boundaries

The module is downstream of `submission_packet.py` and does not replace it. Registration verifies the exact packet digest and requires `READY_FOR_HUMAN_SUBMISSION` with the packet's human-only / no-cash authority intact. It stores only a compact binding: canonical source, packet digest, artifact evidence digest, head SHA, artifact revision, opaque route identity, and optional contract-generation digest.

It is also separate from:

- provider-side outreach dedupe (`outbound_dedupe.py`): pre-send stale-feed / provider truth;
- revenue closeout (`revenue_closeout.py`): merged-work maintainer review and response routing;
- revenue settlement (`revenue_settlement.py`): wallet-provenance payment evidence.

## Lifecycle

A candidate begins as `READY_TO_SUBMIT`. Recording one primary dispatch makes the send disposition permanently `ALREADY_SUBMITTED` for that revision. Silence never becomes acceptance or payment.

With trusted `as_of` and an explicit follow-up interval, dispatched work is either `AWAITING_SPONSOR` or `FOLLOW_UP_DUE`. A recorded follow-up resets the clock but does not create another primary submission.

Sponsor evidence can move the lifecycle to:

- `NEEDS_CHANGES`
- `ACCEPTED_AWAITING_SETTLEMENT`
- `SETTLEMENT_EVIDENCE_READY`
- `CLOSED_REJECTED`
- `WITHDRAWN`

`acknowledged` remains non-acceptance. `payment_pending` and `paid_evidence_ready` are refused until explicit acceptance exists. Terminal response states cannot be reopened by stale later events.

## Supersession

A changed artifact is a new `submission_id` with a strictly higher `artifact_revision`. It does not silently replace the old submission. An explicit supersession event must bind both old/new submission digests and evidence. The old revision remains historical evidence and becomes `SUPERSEDED`; no later dispatch, follow-up, or sponsor event can mutate it.

The same canonical source + artifact revision cannot be registered twice under different IDs, which closes cross-route duplicate candidates. Provider receipt/event identifiers are also globally unique in one ledger.

## Minimal usage

```python
from concierge.submission_custody import (
    empty_ledger,
    register_candidate,
    record_dispatch,
    evaluate_submission,
)

ledger = empty_ledger()
registered = register_candidate(
    ledger,
    event_id="EV-REG-1",
    occurred_at="2026-09-13T10:00:00Z",
    submission_id="SUB-315-v1",
    packet=ready_packet,
    artifact_revision=1,
    route_class="email_fallback",
    route_key="rustchain-sponsor-fallback",
    contract_digest="",
    as_of="2026-09-13T12:00:00Z",
)

receipt = registered["receipt"]
# Human/operator performs the external send outside this module.

dispatched = record_dispatch(
    registered["ledger"],
    event_id="EV-SEND-1",
    occurred_at="2026-09-13T10:05:00Z",
    submission_id="SUB-315-v1",
    submission_digest=receipt["submission_digest"],
    route_class="email_fallback",
    provider_receipt_key="gmail-msg-001",
    evidence_digest="<64 lowercase hex>",
    as_of="2026-09-13T12:00:00Z",
)

status = evaluate_submission(
    dispatched["ledger"],
    "SUB-315-v1",
    as_of="2026-09-14T10:05:00Z",
    follow_up_after_seconds=86400,
)
assert status["send_disposition"] == "ALREADY_SUBMITTED"
assert status["lifecycle_state"] == "FOLLOW_UP_DUE"
```

Use opaque provider references such as a Gmail message ID or GitHub comment ID; do not store email addresses, raw messages, credentials, or tokens in the ledger.

## Integrity and replay

The ledger is append-only and has a canonical SHA-256 over all normalized events. `verify_ledger()` replays the full event stream and rechecks identity, route, chronology, transition, global external-reference uniqueness, and digest invariants. Exact event replay is idempotent; the same event ID with changed bytes fails closed.

`strict_json_loads()` is provided for file/transport boundaries that need duplicate-key rejection before passing objects to the API.

## Authority ceiling

Every status receipt explicitly fixes these authorities to false:

- external send performed by this tool;
- sponsor response inferred;
- acceptance inferred;
- payment inferred;
- payout requested;
- wallet mutated;
- revenue recognized.

A `SETTLEMENT_EVIDENCE_READY` state only means an explicit sponsor event says payment evidence is ready to be verified by the separate settlement rail. It is not a cash claim.
