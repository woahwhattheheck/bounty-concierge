# Later terminal assertions in bounty comments

An earlier negated match previously hid a later explicit acceptance, award,
capacity closure or cancellation in the same clause. `_terminal_signals` now
continues searching after a negated match, stopping at the first non-negated
match for that rule. Search resumes one character after the previous match
start because the existing bounded patterns can overlap.

The rule patterns, 40-character negation context, candidate-line/question filters,
author associations, generation checks, result schema and payout disclaimers are
unchanged. This remains a syntactic dispatch guard, not a natural-language proof
of award or payment.

## Executed result

Twelve controlled cases ran through the real `inspect_bounty_availability`
entrypoint against independently imported original and modified source modules.
All twelve passed: five previously missed terminal assertions now produce HOLD;
seven controls preserve their complete selected output fields. Every case used
exactly four controlled GET calls in each version, with no network traffic.

| Case | Original | Modified |
| --- | --- | --- |
| later_acceptance | CLEAR | HOLD / MAINTAINER_ACCEPTANCE_SIGNAL |
| overlapping_acceptance | CLEAR | HOLD / MAINTAINER_ACCEPTANCE_SIGNAL |
| later_cancellation | CLEAR | HOLD / MAINTAINER_CANCELLED_SIGNAL |
| later_award | CLEAR | HOLD / MAINTAINER_AWARD_SIGNAL |
| later_capacity | CLEAR | HOLD / MAINTAINER_CAP_CLOSED_SIGNAL |
| negated_only | CLEAR | CLEAR |
| no_more | HOLD / MAINTAINER_CAP_CLOSED_SIGNAL | HOLD / MAINTAINER_CAP_CLOSED_SIGNAL |
| negated_stop | CLEAR | CLEAR |
| quoted | CLEAR | CLEAR |
| fenced | CLEAR | CLEAR |
| untrusted_author | CLEAR | CLEAR |
| changed_generation | HOLD / COMMENT_GENERATION_CHANGED | HOLD / COMMENT_GENERATION_CHANGED |

The quoted/fenced/untrusted-author controls remain clear; `no more submissions`
remains terminal; negated-only statements remain clear; independently changed
comment content still produces COMMENT_GENERATION_CHANGED. Public receipts did
not contain the synthetic comment text or author login.

Original module Git blob: `c955dbea56b0bef57a8028f6e2b12cef7041dd41`.
Modified module Git blob: `98458468fb16b460674bfe6d448ad71e7c89e2e5`.
Original source commit: `0aa507c40c7b5685aaa45ae22116359e05787231`.
Python 3.13.5. No dependency installation, live GitHub request or full test suite.
The current classify-once second traversal and HTTP-error cleanup are retained.

```sh
git show 0aa507c40c7b5685aaa45ae22116359e05787231:concierge/bounty_availability.py > /tmp/availability-before.py
PYTHONPATH=. python work/throughput/availability-later-assertion-20261004/replay.py \
  --before /tmp/availability-before.py --output /tmp/availability-results.json
```

The command records the exact input source blobs, interpreter, per-case before
and after dispositions/reasons/signals, and controlled request counts. Normal
availability callers adopt the repair automatically; no new workflow or gate.
