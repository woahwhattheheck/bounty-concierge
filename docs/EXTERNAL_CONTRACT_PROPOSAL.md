# External Contract Proposal Packets

`concierge.contract_proposal` is the evidence-preserving bridge between external
paid-contract qualification and a proposal that an owner can review.

It exists because `contract_qualification.py` intentionally stops at
`ACTIONABLE`: it proves that the listing, bid bounds, platform readiness and
screening evidence are current enough to consider. It does **not** turn that
receipt into prose, a delivery plan or a marketplace submission. This module
closes only the packet-assembly gap.

## Authority boundary

The proposal packet is decision support. It does not submit a bid or proposal,
contact a customer, accept marketplace terms, create or mutate an account,
complete KYC, spend funds, accept a contract, claim an award, infer payment, or
recognize revenue. `CONNECTED_ACTION` is preserved only as the qualified route;
the emitted packet still says `READY_FOR_OWNER_REVIEW` and
`human_review_required=true`.

A packet SHA-256 is self-integrity only. `verify_external_contract_proposal()`
can detect packet tampering or an authority-shape change, but it does not
recreate provider authority. A caller deciding whether a proposal is still
usable must build it from the original snapshot + qualification receipt at a
trusted current time, which calls
`verify_contract_qualification_receipt(...)` and re-evaluates freshness.

## What is bound

The builder refuses a stale or non-`ACTIONABLE` qualification and then binds:

* the canonical source URL and source digest;
* the exact qualification digest;
* the exact native currency, bid amount and delivery-day window from the
  qualification receipt (the proposal brief has no independent price field);
* every structured capability claim to one or more `EXACT` evidence IDs that
  already belong to that same qualification claim;
* every mandatory qualification claim to exactly one proposal-claim row;
* every deliverable to exactly one milestone;
* every milestone to a strictly increasing due day inside the qualified
  delivery window; and
* the submission route, while retaining owner review and zero external-action
  authority.

`ADJACENT` evidence can be useful during screening but cannot be promoted into
proposal proof here. Evidence cannot be moved from one claim to another.

The free-form `headline` and `cover_note` are operator-authored presentation
text and are not promoted into independent evidence. Auditable capability
claims live in `proposal_claims`, where evidence bindings are mandatory.

## Brief schema

```json
{
  "schema_version": "bounty-concierge.external-contract-proposal-brief/v1",
  "proposal_id": "acme-api-2026-09",
  "headline": "Evidence-backed API automation delivery",
  "cover_note": "I can deliver the scoped work inside the qualified window.",
  "proposal_claims": [
    {
      "claim_id": "python-experience",
      "statement": "Relevant Python delivery experience is evidenced below.",
      "evidence_ids": ["merged-pr-128"]
    }
  ],
  "deliverables": [
    {
      "deliverable_id": "implementation",
      "description": "Production implementation",
      "acceptance_note": "Meets the agreed functional scope"
    },
    {
      "deliverable_id": "tests",
      "description": "Focused regression coverage",
      "acceptance_note": "Targeted tests pass"
    }
  ],
  "milestones": [
    {
      "milestone_id": "m1",
      "title": "Implementation and validation",
      "due_day": 10,
      "deliverable_ids": ["implementation", "tests"]
    }
  ]
}
```

The `claim_id` and `evidence_ids` values must already exist in the verified
qualification receipt. A mandatory claim omitted from the proposal fails the
build rather than silently producing an incomplete sales claim.

## CLI

```bash
python -m concierge.contract_proposal \
  qualification-snapshot.json \
  qualification-receipt.json \
  proposal-brief.json \
  --as-of 2026-09-13T13:00:00Z \
  --json
```

`--as-of` is passed into the qualification verifier as trusted current time.
Production callers must not let an untrusted marketplace payload choose it.

File inputs are read from already-opened regular-file descriptors with a 1 MiB
cap. On platforms with `O_NOFOLLOW`, symlink opens are refused. Stdin is also
bounded and at most one positional input may be `-`.

## Output semantics

Successful construction emits `bounty-concierge.external-contract-proposal/v1`
with:

* `status=READY_FOR_OWNER_REVIEW`;
* the exact qualified bid and route;
* deterministic normalized structured claims, deliverables and milestones;
* `human_review_required=true` and `submitted=false`;
* an all-false authority map for marketplace submission, customer contact,
  terms/account/KYC/spend/contract acceptance, award, payment and revenue; and
* `packet_sha256`, a deterministic integrity hash over the packet core.

A successful packet means *assembly is internally consistent with a currently
valid qualification*. It does not mean the customer has seen it, the bid was
submitted, the contract was won, work was accepted, or cash was received.

## Failure modes that intentionally HOLD/raise

The builder fails closed for, among other cases:

* qualification receipt mismatch, expiry or drift away from `ACTIONABLE`;
* previously submitted qualification input;
* qualification/verification digest disagreement;
* native-currency mismatch;
* missing mandatory proposal claims;
* unknown, cross-claim or `ADJACENT` evidence references;
* duplicate proposal claims, deliverables or milestones;
* deliverables omitted from milestones or assigned more than once;
* milestone order regression or a due day beyond the qualified delivery window;
* undeclared brief fields; and
* malformed, oversized or non-regular CLI inputs.

These fences are deliberate: the module should make the revenue pipeline faster
without turning an internally consistent JSON blob into external authority.
