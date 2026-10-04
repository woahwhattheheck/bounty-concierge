# Human-only bounty submission packet

`concierge.submission_packet` converts a row already selected by the canonical qualified-opportunity portfolio into an evidence-bound handoff for a human submitter. It **does not** post a GitHub claim/comment, open a PR, infer maintainer acceptance, or assert that advertised reward has been earned or paid.

Input is a JSON object with exactly `portfolio` and `evidence`. The portfolio must use `qualified-opportunity-portfolio/v1`, explicitly carry `cash_claim: false`, and selected rows must retain `advertised_only` reward authority plus `not_earned_or_settled_by_this_receipt` revenue authority. Evidence is recipient-scoped by canonical GitHub issue URL and binds a canonical PR URL, exact lowercase 40-hex head SHA, changed paths and their allowlist, terminal test outcomes, a lowercase SHA-256 evidence digest, and criterion IDs with explicit acceptance-check statuses.


GitHub owner and repository capitalization is ignored when matching selected
issues to their evidence and detecting duplicate sources. The selected source
URL, PR URL, sponsor instruction record and existing packet hash format retain
their original spelling. A different issue number or repository remains a
different source.

A selected row is `READY_FOR_HUMAN_SUBMISSION` only when evidence exists, every changed path is allowed, every declared test is `PASS`, and every acceptance check is `PASS`. Otherwise it is `HOLD` with deterministic reason codes. Extra or duplicate evidence, noncanonical URL aliases, path traversal, malformed hashes, or invalid authority fail the request closed.

```bash
python -m concierge.submission_packet request.json
python -m concierge.submission_packet request.json --summary
```

The output packet repeats only bounded structured evidence and emits `packet_sha256`; it does not copy raw bounty issue bodies/comments. A human remains the sole external submission authority.

## Bounty boards and delivery repositories

By default, the PR must belong to the bounty issue's repository. When the sponsor
explicitly directs delivery to another repository, add `submission_target` to the
opportunity candidate passed to `concierge.portfolio_allocator`, or to its already
selected portfolio row. The allocator preserves it through selection; the packet
builder and submission custody use the same validation and retain it in the
packet's SHA-256 binding.

The record contains exactly four fields:

| Field | Value |
| --- | --- |
| `repository` | Explicit delivery repository in `owner/name` form. |
| `source_url` | The canonical bounty issue URL, or a comment on that same issue using `#issuecomment-N`. |
| `source_content_sha256` | Lowercase SHA-256 of the retained sponsor source content. |
| `instruction_excerpt` | The sponsor's bounded, single-line delivery instruction. |

For example, [rustchain-bounties #30](https://github.com/Scottcjn/rustchain-bounties/issues/30)
directs a PR to `Scottcjn/Rustchain`. Its issue remains the canonical bounty source;
`Scottcjn/Rustchain` is the explicit delivery target. Preserve the observed source
and its digest when recording that instruction. This example identifies a routing
requirement and does not establish a claim, USD reward, completed work, or payment.

An arbitrary link in issue text never selects a target. Without this record,
cross-repository PRs still fail. With it, the PR must match the named repository;
an unrelated source issue, malformed digest, or missing instruction fails.
The record is operator-supplied evidence, not independent authentication of sponsor
intent, eligibility, or acceptance. Verify sponsor instructions through the normal
intake process. Existing same-repository inputs and packet output remain unchanged
when `submission_target` is absent.
