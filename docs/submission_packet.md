# Human-only bounty submission packet

`concierge.submission_packet` converts a row already selected by the canonical qualified-opportunity portfolio into an evidence-bound handoff for a human submitter. It **does not** post a GitHub claim/comment, open a PR, infer maintainer acceptance, or assert that advertised reward has been earned or paid.

Input is a JSON object with exactly `portfolio` and `evidence`. The portfolio must use `qualified-opportunity-portfolio/v1`, explicitly carry `cash_claim: false`, and selected rows must retain `advertised_only` reward authority plus `not_earned_or_settled_by_this_receipt` revenue authority. Evidence is recipient-scoped by canonical GitHub issue URL and binds a canonical PR URL, exact lowercase 40-hex head SHA, changed paths and their allowlist, terminal test outcomes, a lowercase SHA-256 evidence digest, and criterion IDs with explicit acceptance-check statuses.

A selected row is `READY_FOR_HUMAN_SUBMISSION` only when evidence exists, every changed path is allowed, every declared test is `PASS`, and every acceptance check is `PASS`. Otherwise it is `HOLD` with deterministic reason codes. Extra or duplicate evidence, URL aliases, path traversal, malformed hashes, or invalid authority fail the request closed.

```bash
python -m concierge.submission_packet request.json
python -m concierge.submission_packet request.json --summary
```

The output packet repeats only bounded structured evidence and emits `packet_sha256`; it does not copy raw bounty issue bodies/comments. A human remains the sole external submission authority.
