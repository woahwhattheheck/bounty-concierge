# Collection custody

`concierge.collection_custody` closes the evidence gap between a verified
`bounty-collection-request/v1` packet and later settlement review. It does not
send messages. It records a strict, append-only history of what an operator can
prove was dispatched and what sponsor evidence was later observed.

## Why this exists

`collection_request.py` deliberately stops at a send-ready draft. A payout URL,
email address, or rendered body is not evidence that a sponsor was contacted.
Without durable custody, the same request can be reconstructed from Gmail or
Slack by hand, resent by another worker, or treated as followed up even though
the original send never occurred.

This rail makes those distinctions explicit:

1. Verify the exact collection packet again and register its immutable packet,
   PR/head, native amount/currency, acceptance generation, payout-route digest,
   contact-route class and opaque route key.
2. Record one primary dispatch only when there is a separate provider receipt
   key plus a SHA-256 commitment to the supporting evidence.
3. Record bounded follow-ups only after a real primary dispatch and before a
   sponsor decision.
4. Record sponsor events from separate evidence. Silence never produces an
   acknowledgement, acceptance, award, payment state, or cash claim.
5. An assessment acceptance/award produces `PAYMENT_REQUEST_REQUIRED`. A new
   `READY_TO_REQUEST_PAYMENT` packet must be registered and explicitly linked
   to the assessment generation before the payment request can be sent.
6. `paid_evidence_ready` means only that payment evidence is ready for the
   settlement verifier. It is not cash or revenue.

## Generations and dedupe

A packet receipt can be registered only once. Provider receipt/event keys are
globally unique opaque identifiers, and one request generation can have at most
one primary dispatch. A second active request for the same PR/head/advertised
amount/currency cannot dispatch until the older assessment generation is
explicitly superseded by a payment generation.

The assessment-to-payment supersession is intentionally strict:

- same canonical PR, exact merged head, advertised native amount and currency;
- same payout-route digest (the acceptance upgrade cannot silently redirect the
  destination);
- assessment must already have a separately evidenced `accepted` or `awarded`
  sponsor event;
- new packet acceptance kind must match that event; and
- both the new packet and supersession event must bind the same evidence digest.

If a payout route legitimately needs to change, that is a distinct owner-reviewed
operation; this bridge does not silently compose it into acceptance escalation.

## Lifecycle

Typical assessment flow:

`READY_TO_SEND -> AWAITING_SPONSOR -> FOLLOW_UP_DUE`

A sponsor decision moves the generation to one of:

- `PAYMENT_REQUEST_REQUIRED` for accepted/awarded assessment;
- `NEEDS_CHANGES`;
- `CLOSED_REJECTED`.

After valid supersession, the payment generation becomes `READY_TO_SEND` and,
once provider dispatch evidence is recorded, may progress through
`AWAITING_SPONSOR`, `FOLLOW_UP_DUE`, `PAYMENT_PENDING`, or
`SETTLEMENT_EVIDENCE_READY`.

A registered same-work generation that is not the dispatched active generation
is held at `LINK_GENERATION_REQUIRED`, preventing independent workers from
parallel-sending two asks.

## Authority ceiling

The module is offline and non-authenticating. It never:

- sends email, comments, forms, or provider messages;
- authenticates a caller-provided provider receipt or sponsor event;
- mutates a provider, wallet, checkout, invoice, refund, or payment rail;
- infers sponsor response, acceptance, award, payout, or payment from silence;
- equates an advertised amount with a debt, receivable, earned revenue, or cash;
- converts currencies or asserts accounting/tax treatment.

The evaluation receipt carries those authority limits explicitly. External
provider authenticity belongs to the provider-owning host; cash authority
belongs to the settlement verifier.

## Integrity and hostile-input rules

The ledger and every request generation are SHA-256 committed with canonical
JSON. Verification replays every event and rejects malformed schemas,
duplicate/mutated event IDs, duplicate packet identities, provider-reference
reuse, future events, chronology inversions, route mismatch, invalid state
transitions, noncanonical PR URLs, invalid native amounts/currencies, tampered
request digests, and authority-expanding assessment/payment transitions.

`strict_json_loads()` rejects duplicate object keys and non-finite JSON
constants at every depth.
