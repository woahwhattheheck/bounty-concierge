# Feedback remediation packets

`concierge.feedback_remediation` is the offline bridge between authoritative
human change-request evidence and the next repair generation of paid work.

It exists because two different facts must not be conflated:

1. `revenue_closeout` can reacquire canonical GitHub maintainer feedback and
   say that repair/response work is currently required.
2. `submission_custody` can record that an externally submitted revision is in
   a coarse `NEEDS_CHANGES` state.

Neither fact, by itself, is a versioned repair plan. This module compiles
**caller-supplied normalized feedback facts** into a deterministic packet that
binds each obligation to the exact submitted head/revision and evidence digest,
then carries unresolved obligations forward until explicit successor evidence
marks them addressed.

The module is deliberately offline. It does not fetch GitHub, infer who is a
maintainer, contact a sponsor, submit a patch, request a payout, mutate a
wallet, or recognize revenue.

## Authority boundary

The caller must already have established the feedback authority. Supported
explicit authority classes are:

- `GITHUB_OWNER`
- `GITHUB_MEMBER`
- `GITHUB_COLLABORATOR`
- `VERIFIED_SPONSOR`

Supported feedback kinds are:

- `CHANGES_REQUESTED`
- `ACTIONABLE_COMMENT`
- `NEEDS_CHANGES`

Every input event must also carry an explicit disposition:

- `REQUIRED`
- `CLARIFY`
- `NON_BLOCKING`

The compiler never performs sentiment analysis and never upgrades arbitrary
text into maintainer/sponsor authority.

Every packet fixes these authority fields to `false`:

- `external_send_performed`
- `feedback_authority_inferred`
- `maintainer_intent_inferred`
- `scope_change_authorized`
- `acceptance_inferred`
- `payment_inferred`
- `payout_requested`
- `wallet_mutated`
- `revenue_recognized`

A repair packet is planning evidence only.

## Compile a first repair generation

Input schema: `bounty-feedback-remediation-input/v1`.

```json
{
  "schema": "bounty-feedback-remediation-input/v1",
  "submission": {
    "submission_id": "SUB-42-v1",
    "canonical_source": "github:acme/widgets#42",
    "submitted_head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "artifact_revision": 1,
    "artifact_evidence_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "submission_digest": null
  },
  "feedback": [
    {
      "source_event_id": "review-101",
      "authority": "GITHUB_MEMBER",
      "kind": "CHANGES_REQUESTED",
      "occurred_at": "2026-09-13T14:20:00Z",
      "evidence_digest": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
      "summary": "Add a regression test for the null response.",
      "disposition": "REQUIRED",
      "target_head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "target_artifact_revision": 1,
      "hints": [
        {
          "path": "src/client.py",
          "line_start": 40,
          "line_end": 45
        }
      ]
    }
  ]
}
```

Run:

```bash
python -m concierge.feedback_remediation compile feedback.json > remediation.json
python -m concierge.feedback_remediation verify remediation.json
```

The compiler normalizes timezone-aware timestamps to UTC, canonicalizes hint
ordering, deduplicates exact event replay, rejects conflicting reuse of one
provider event ID, and emits a stable obligation ID plus an event fingerprint.

The exact `target_head_sha` and `target_artifact_revision` in every feedback
event must match the submission being compiled. Feedback from another
generation fails closed instead of being silently replayed.

## Advance after a repair

Input schema: `bounty-feedback-remediation-advance/v1`.

```json
{
  "schema": "bounty-feedback-remediation-advance/v1",
  "prior_packet": {},
  "successor_submission": {
    "submission_id": "SUB-42-v2",
    "canonical_source": "github:acme/widgets#42",
    "submitted_head_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "artifact_revision": 2,
    "artifact_evidence_digest": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "submission_digest": null
  },
  "addressed": [
    {
      "obligation_id": "obl-REPLACE-WITH-REAL-ID",
      "evidence_digest": "1111111111111111111111111111111111111111111111111111111111111111",
      "note": "Regression test and null guard added."
    }
  ],
  "new_feedback": []
}
```

`prior_packet` is the complete verified packet from the previous generation.
Run:

```bash
python -m concierge.feedback_remediation advance advance.json > remediation-v2.json
python -m concierge.feedback_remediation verify remediation-v2.json
```

Advancement requires:

- the canonical source remains unchanged;
- `artifact_revision` strictly increases;
- the exact head SHA changes;
- the artifact evidence digest changes;
- an obligation can become `ADDRESSED` only when the caller supplies a
  64-hex successor evidence digest;
- already-addressed obligations cannot be addressed again;
- unknown obligation IDs fail closed;
- unresolved obligations are carried automatically rather than disappearing;
- new feedback must bind to the successor head/revision;
- a previously used `source_event_id` cannot be rebound to different bytes.

Addressed obligations remain in the packet history with the exact successor
head/revision and evidence digest that justified the disposition change.

## Integrity

Packets use schema `bounty-feedback-remediation-packet/v1`.

`packet_digest` is SHA-256 over canonical UTF-8 JSON with that field set to an
empty string. `verify_packet()` recomputes all normalized event fingerprints,
stable obligation IDs, counts, ordering, successor resolution invariants, the
authority ceiling, and the outer packet digest.

`verify_chain()` additionally proves that each generation's
`predecessor_packet_digest` exactly matches the preceding packet, that
generations are contiguous, that canonical source identity does not change,
and that artifact revision increases monotonically.

The digest is an integrity mechanism, not an authority source. Re-hashing a
packet with a forbidden authority flag does not make it valid.

## Ingress hardening

`strict_json_loads()` / `load_json()` reject:

- duplicate JSON object keys;
- `NaN`, `Infinity`, and all JSON floating-point values;
- oversized inputs, strings, containers, integers, or nesting;
- malformed UTF-8;
- unknown schema keys;
- malformed SHA/evidence digests;
- unsafe path hints (`..`, absolute paths, backslashes);
- naive timestamps without a timezone.

This keeps a feedback packet bounded and deterministic before it enters a
repair workflow.

## Composition with existing rails

Use this module **after** an authority layer has normalized feedback:

- `revenue_closeout`: canonical GitHub maintainer review/comment discovery;
- `submission_custody`: sponsor-response lifecycle evidence.

Do not feed arbitrary scraped text directly into remediation authority.

Use the produced repair packet as planning/verification input for a later code
generation. External dispatch and sponsor communication remain human/provider
operations outside this module. Acceptance, payout, payment, and realized cash
remain separate evidence domains.
