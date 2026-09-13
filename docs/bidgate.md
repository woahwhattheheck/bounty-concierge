# BidGate — deterministic RFP qualification authority

BidGate answers one bounded question before proposal work or outreach consumes time:

> **Does the explicit evidence in this manifest support a direct-prime posture, require a named partner, prove a no-go, or require a hold?**

It is intentionally separate from `bounty_qualification.py`. The existing bounty gate decides whether a GitHub bounty is actionable. BidGate models procurement/RFP qualification boundaries such as mandatory references, insurance, certifications, partner-permitted experience, scoring thresholds, question deadlines, proposal deadlines, and buyer-contact restrictions.

## Dispositions

- `PRIME_READY` — the named prime itself satisfies every mandatory prime-only gate, every mandatory partner-allowed gate is satisfied, and the scored minimum is met.
- `TEAM_REQUIRED` — all gates are satisfied, but at least one requirement marked `partner_allowed` is satisfied only by a named partner.
- `NO_GO` — a mandatory gate is definitively unsatisfied, the scored minimum is definitively unmet, or the proposal deadline has passed.
- `HOLD` — evidence relevant to a decision exists but is not currently authoritative, such as expired evidence or conflicting current evidence.

A stronger score **never** overrides a failed mandatory gate.

## Authority boundary

Every result emits explicit false authority for buyer contact, submission, signature, contract, spend, and revenue recognition. `safe_next_action` is a human-preparation state only. BidGate does not send email, touch a portal, sign anything, purchase anything, bind a partner, or claim revenue.

`contact_policy` is carried verbatim from the opportunity manifest. Clarification questions are emitted only while `decision_time < question_deadline`; the engine never treats a generated question as permission to contact anyone.

## Requirement model

Each opportunity requirement has:

- unique `id`;
- `level`: `mandatory` or `scored`;
- `authority`: `prime_only` or `partner_allowed`;
- `evidence_type`;
- a rule: `present`, `equals`, or integer `gte`;
- positive integer `weight` for scored requirements only;
- optional `question` for a still-open clarification window.

`partner_allowed` is explicit. BidGate never assumes that subcontractor/partner experience counts for the prime.

## Evidence model

The manifest must contain exactly one `prime`; zero or more named `partner` participants may follow. Evidence atoms are explicit and immutable-input-like:

- globally unique evidence `id`;
- evidence `type` and scalar `value` (string, integer, or boolean; floats are deliberately rejected);
- non-empty `source` locator/description;
- timezone-aware `observed_at`;
- optional timezone-aware `expires_at` later than observation;
- lowercase `provenance_sha256` supplied by the caller for the cited artifact/receipt.

Evidence observed after `decision_time` is invalid. Evidence expired by `decision_time` cannot qualify a gate. Multiple current atoms of the same relevant type for one participant that disagree on value are ambiguous and force `HOLD` unless another authoritative current satisfier resolves the gate.

BidGate does not fetch or infer evidence. A person or upstream evidence collector must decide what artifact is authoritative and supply its provenance digest.

## Content-addressed receipt

Every result includes:

- `opportunity_digest` over the normalized opportunity requirements/policy;
- `evidence_digest` over normalized participants and evidence atoms;
- `decision_digest` over the complete decision payload plus both input digests.

JSON canonicalization uses sorted keys, compact separators, UTF-8, and disallows NaN/infinity. Replaying the same manifest yields byte-identical canonical JSON. Requirement or evidence substitution changes the receipt.

## CLI

```bash
python -m concierge.bidgate data/bidgate_example.json --pretty
```

Exit codes:

- `0`: `PRIME_READY` or `TEAM_REQUIRED`
- `2`: `HOLD`
- `3`: `NO_GO`
- `64`: invalid/unreadable manifest

The CLI emits only the decision object on success; parse/load errors are reduced to a bounded error message on stderr.

## Synthetic example

`data/bidgate_example.json` is deliberately synthetic. Its prime has insurance + technical-fit evidence while a named partner supplies the explicitly partner-allowed reference gate, so the expected disposition is `TEAM_REQUIRED`.

## Acceptance

```bash
python -m unittest -v tests.test_bidgate
python -m concierge.bidgate data/bidgate_example.json --pretty
```

The test matrix covers direct-prime success, partner-only satisfaction, prime-only isolation, mandatory-vs-score precedence, stale/ambiguous evidence, duplicate IDs, future observations, non-integer/nonfinite score input, score threshold, question deadline, proposal deadline, content-addressed substitution, deterministic replay, and CLI exit/error behavior.
