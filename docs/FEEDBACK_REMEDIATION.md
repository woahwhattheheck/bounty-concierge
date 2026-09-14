# Feedback remediation chains

`concierge.feedback_remediation` turns already-authoritative paid-work feedback into deterministic remediation obligations without inferring maintainer intent, acceptance, payment, or revenue.

## Why v2 exists

A packet hash proves only that one packet has not changed. It does **not** prove that an operator preserved earlier obligations. The predecessor carrier could verify adjacent generations, but its production `advance` and `verify` commands accepted a single packet, so a self-resealed packet with dropped/re-written history could become the next starting point. It also allowed `ADDRESSED` to be minted from an arbitrary 64-hex label unrelated to the successor artifact manifest.

Version 2 makes those impossible in the supported workflow:

1. `advance` consumes the complete prior chain, not one prior packet.
2. `advance` also requires an independently retained expected prior chain-head digest (`--expected-prior-head-digest`). The digest is supplied separately from the JSON chain and must equal the verified last packet.
3. `verify` consumes the complete chain and requires the independently retained expected head digest (`--expected-head-digest`). There is no CLI that represents standalone packet self-integrity as continuity authority.
4. An `ADDRESSED` transition must name the **exact `artifact_evidence_digest` from the successor submission**. Arbitrary or unrelated 64-hex values cannot close an obligation.
5. `advance` re-runs the same chain verifier over the candidate successor before returning it.

The expected head digest is an external custody anchor. Store it separately from the packet chain (for example in the surrounding workflow state or immutable receipt log). If both the chain and its expected-head value are supplied by the same untrusted party, the module cannot manufacture trust that was never retained.

## Schemas

- compile input: `bounty-feedback-remediation-input/v1`
- advance input: `bounty-feedback-remediation-advance/v2`
- packets: `bounty-feedback-remediation-packet/v2`

A submission binds:

- `submission_id`
- `canonical_source`
- exact 40-hex `submitted_head_sha`
- monotonic `artifact_revision`
- exact 64-hex `artifact_evidence_digest`
- optional `submission_digest`

Feedback events are normalized facts from an upstream authority layer. Every event is bound to the exact submitted head and artifact revision. Duplicate identical source events are idempotent; conflicting reuse of a `source_event_id` fails closed.

## Compile

```bash
python -m concierge.feedback_remediation compile remediation-input.json > remediation-v1.json
```

Persist the returned `packet_digest` separately. That digest is the expected prior head for the next advance.

## Advance

The v2 advance JSON contains `prior_chain`, `successor_submission`, `addressed`, and `new_feedback`.

Each addressed item is:

```json
{
  "obligation_id": "obl-...",
  "successor_artifact_evidence_digest": "<exact digest from successor_submission>",
  "note": "optional human note"
}
```

Advance with the independently retained previous head:

```bash
python -m concierge.feedback_remediation advance remediation-advance-v2.json \
  --expected-prior-head-digest "$EXPECTED_PRIOR_HEAD" > remediation-v2.json
```

The transition is rejected if:

- any prior packet is missing, reordered, or has a broken predecessor link;
- any prior obligation disappears or its immutable evidence changes;
- an addressed obligation is reopened/rewritten;
- a new obligation is backdated or targets another head;
- the successor head/revision/artifact evidence does not advance;
- a resolution digest differs from the successor submission's exact artifact evidence digest;
- the verified chain head differs from the separately retained expected prior head.

## Verify

Put the complete chain in one JSON array and verify it against the independently retained expected head:

```bash
python -m concierge.feedback_remediation verify remediation-chain.json \
  --expected-head-digest "$EXPECTED_CHAIN_HEAD"
```

`verify_packet()` remains an internal/library self-integrity primitive. A self-resealed forged packet can be internally consistent; it is **not** continuity proof. Production `verify` deliberately exposes only full-chain verification.

## Authority ceiling

The packet hard-binds the following to false:

- external send performed
- feedback authority inferred
- maintainer intent inferred
- scope change authorized
- acceptance inferred
- payment inferred
- payout requested
- wallet mutated
- revenue recognized

The module does not contact GitHub, sponsors, customers, payment providers, wallets, or bounty providers. It does not claim work, send remediation, infer acceptance, or recognize cash. `chain_head_is_external_authority=true` means continuity depends on the separately retained expected digest; it does not mean the module authenticates the upstream feedback facts.

## Hostile regression coverage

The focused suites cover:

- self-resealed successor dropping all prior obligations;
- rewritten historical evidence with a valid new packet digest;
- addressed obligations being reopened;
- backdated obligation injection;
- arbitrary/unrelated 64-hex resolution evidence;
- truncated-chain replay against a retained head digest;
- forged prior-head replay against a retained head digest;
- three-generation valid history;
- wrong external chain-head anchors;
- source-event conflict/idempotence;
- exact head/revision binding;
- changed successor head and artifact evidence requirements;
- duplicate JSON keys, floats/non-finite numbers, traversal hints, tampering, and bounded file ingress;
- normal and `python -O` execution on Python 3.9 and 3.13.
