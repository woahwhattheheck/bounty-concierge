# Bounty Availability Guard

`issue.state == "open"` is not sufficient authority to start or claim new paid work.

A sponsor can leave an issue open after accepting a submission, awarding a
slot, exhausting bounty capacity, or cancelling the offer. On 2026-09-13,
`Scottcjn/rustchain-bounties#315` provided the concrete failure mode: the issue
was still open while a maintainer comment explicitly recorded an accepted
Stage-1 pack for 30 RTC. Building or claiming another full pack from open-state
evidence alone wastes operator time and can create duplicate-claim risk.

## Installed claim path

The installed `concierge claim` command now performs two independent live
authority checks before claim instructions can be emitted:

1. canonical bounty preflight / qualification;
2. canonical maintainer terminal-outcome availability.

If preflight is already HOLD or REJECT, availability is not queried. If
preflight is ACTIONABLE but availability returns HOLD, claim output is blocked
with a privacy-safe `AVAILABILITY:<reason>` qualification. Dry-run, help, and
non-claim commands preserve their existing no-network behavior.

Operators can inspect the composed new-work decision directly with:

```bash
python -m concierge.revenue_dispatch OWNER/REPO ISSUE --json
```

The lower-level availability authority is also directly inspectable:

```bash
python -m concierge.bounty_availability OWNER/REPO ISSUE --json
```

## Authority model

Only canonical GitHub issue comments authored with GitHub
`OWNER`, `MEMBER`, or `COLLABORATOR` association can create terminal signals.
External-user prose never does.

The guard recognizes narrow, explicit syntactic families:

- accepted/winner language bound to a submission, claim, PR, work item, pack, or
  GitHub mention;
- explicit award-to language;
- explicit full/closed/filled/exhausted capacity or "no more submissions";
- explicit bounty/reward/task cancellation, withdrawal, or voiding.

Questions, quoted lines, fenced code, and locally negated statements are ignored
to reduce false positives. A terminal match produces `HOLD`, not `REJECT`: it
requires human review before new work or claim emission and does **not** prove
payout, winner identity, legal entitlement, collected cash, or recognized
revenue.

## Generation and privacy fences

A CLEAR result requires two complete reads from the canonical issue-comments
endpoint. Every comment generation marker binds:

- comment id;
- user identity internally (not emitted);
- GitHub author association;
- creation and update timestamps;
- SHA-256 of the exact body.

This catches same-timestamp semantic edits. The issue itself is also bound by
id, number, state, comment count, update timestamp, and title/body digests
before and after the comment reads.

Any pagination truncation, malformed evidence, issue-generation change, or
comment-generation change fails closed. Safe receipts never include raw comment
text or user logins; they expose only terminal signal classes and canonical
comment ids needed for operator follow-up. The installed claim boundary further
reduces a HOLD to reason and signal classes only; comment ids and evidence
objects are not emitted in claim-blocked output.

## Classify each stable comment generation once

The first comment traversal records terminal maintainer signals. The second
traversal independently validates and hashes every comment, but skips the
unused repeat of text classification. Both live traversals, issue reads,
pagination checks, generation comparisons, count checks, and duplicate-ID
checks remain in place. A change between traversals still prevents CLEAR.

A local replay on October 4, 2026 used the actual retained response for
`moorcheh-ai/memanto#1852`: 48 comments, including 9 maintainer-associated
comments. Both source versions returned the same complete receipt and made
the same four canonical requests through Requests to a loopback HTTP server.
Classifier calls fell from 18 to 9, while both passes produced identical
generation markers and the first-pass terminal signals were unchanged.

Against source blob `c3b25c1e1e7a05670de53b398dfb40b1dd2f1051`, median CPU time
for preparing both passes of comment evidence fell from 1.624 ms to 0.863 ms:
about 0.76 ms saved per check of this page (46.8%). Nine alternating sample
pairs each repeated the same retained input 100 times; the history itself was
not expanded. This measures local evidence preparation only, excluding HTTP,
JSON decoding, and package startup, using Python 3.12.14 and Requests 2.34.2.
It does not measure provider latency, API quota savings, or fleet throughput.
The retained comment response SHA-256 was
`a88ae3c6faf196273b82d8b6cea44ba01d8c27ae872495e5bb654ed0e6b81625`.

## HTTP connection reuse

The default availability check owns one `requests.Session` for its issue reads
and both paginated comment traversals. It closes that session when the check
returns or raises, including an early HOLD for a closed issue. Argument
validation runs before the session is allocated. Callers that supply a session
keep ownership and can reuse it for later checks.

Complete checks still perform both live history traversals and generation
comparisons. For 101 comments across two pages, six GETs share one connection
pool instead of creating six separate pools. There is no added cache, retry,
or delay.

Local HTTP/1.1 replay against baseline `73e3b4f` measured six accepted TCP
connections before this change and one after it, with six requests in both
runs. The replay used the existing synthetic 101-comment fixture in
`work/throughput/contract-session-reuse-20261003/benchmark.py`, Python 3.12.14,
and Requests 2.34.2. Complete receipts and request paths matched for stable,
closed-issue, and comment-drift cases; provider-error behavior and session
cleanup also matched. Caller-supplied sessions remained open across successful,
early-return, and error paths. This measures local connection reuse, not live
GitHub latency or rate-limit savings.

## Stop after an incomplete first traversal

If the first comment traversal reaches `max_pages` while another page remains,
the guard immediately returns `HOLD / COMMENT_HISTORY_TRUNCATED` with
`dispatch=false`. It does not repeat that already-incomplete traversal or
request a final issue snapshot. No later response could make the first
traversal complete, and a later HTTP error must not erase this known result.
The receipt's `issue_state` is the initial validated observation, not a claim
that the issue state was reread after truncation. Signal evidence stays empty;
partial comments are not treated as complete availability or payout evidence.

With `max_pages=10` and ten pages still advertising a next page, this reduces
one check from 22 GETs to 11. With `max_pages=1` and a nonterminal first page,
it reduces four GETs to two. Minimal injected transports without response
headers retain the older length-based fallback: a full page at the bound still
means HOLD. Completeness is never inferred from the declared comment count.
Second-traversal truncation, generation comparisons, comment validation,
closed-issue handling and session ownership are unchanged. These counts
describe the request path, not live provider latency or cash saved.

## Provider pagination, including full terminal pages

GitHub's comment response headers now determine whether another page remains.
A `Link` relation containing `next` continues the traversal even when the page
has fewer than 100 rows. A response with no `Link` header, or a valid pagination
header containing only other relations, ends it even when the page is full.
This follows GitHub's documented [REST pagination contract](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api).

Only GitHub's pagination link syntax is accepted; malformed supplied headers
raise the existing `BountyAvailabilityError` rather than establish completeness.
Link destinations are not followed: requests stay on the canonical comments
endpoint with sequential numeric pages. Header-less minimal injected
transports retain the length fallback described above. There is no new retry,
sleep, cache, concurrency gate, permission, or operator step.

A complete 100-comment history can therefore fit `--max-pages 1`. It still
needs both comment traversals, matching before/after issue generations,
matching comment generations and counts, unique comment IDs, and no terminal
maintainer signal before returning CLEAR. A 101-comment history at that same
bound remains HOLD after two GETs.

### Measured loopback replay, October 4, 2026

The complete source module was executed with Python 3.13.5 and Requests 2.32.5
against a synthetic HTTP/1.1 server on 127.0.0.1. Baseline source blob:
`5e92de31093aa58dd100a4b8d5d6ecd817accf84`; candidate source blob:
`26203721782586a13e6aa2e0b3e3d5934b094888`.

| Synthetic history | Before | After | Outcome |
| --- | ---: | ---: | --- |
| Exactly 100 comments, terminal page | 6 GETs | 4 GETs | Same complete receipt; 33.3% fewer requests |
| Exactly 200 comments, terminal second page | 8 GETs | 6 GETs | Same complete receipt; 25% fewer requests |
| 100 comments, max-pages 1 | 2 GETs, HOLD | 4 GETs, CLEAR | False truncation removed; both scans now complete |
| 200 comments, max-pages 2 | 3 GETs, HOLD | 6 GETs, CLEAR | False truncation removed; both scans now complete |
| 101 comments, max-pages 1 | 2 GETs | 2 GETs | Same truncated HOLD |
| Short first page with explicit next | 4 GETs, count mismatch | 6 GETs, CLEAR | Remaining comments now read |

The replay checks 18 candidate scenarios and two metadata-free fallback
assertions, plus 16 baseline scenarios. Outside the three intentional
pagination corrections, complete returned receipts matched. Closed issues,
maintainer closure, comment/issue drift, count mismatch and duplicate IDs still
hold; HTTP 429 stops without retry; malformed headers fail; a foreign next URL
is never requested. The script prints source hashes, actual request paths,
counts and receipts for independent inspection:

```bash
python tools/replay_availability_pagination.py
# Optional historical comparison, with the exact baseline source:
git show bf59b8289f6a52903fe8727baf89b96b747be299:concierge/bounty_availability.py > /tmp/availability-before.py
python tools/replay_availability_pagination.py --source /tmp/availability-before.py --baseline
```

Only deployment token configuration was replaced with `None`; no classifier or
reader implementation was substituted. The unrelated package policy bootstrap
was not imported. These are local request-count and behavior measurements, not
live GitHub latency, fleet-wide quota savings, installed-CLI integration,
provider acceptance or revenue measurements. No provider or account calls,
new dependencies, CI jobs or owner-PC execution were used for this replay.

## Non-authority

This guard does not:

- create or post a claim;
- decide whether a bounty is legitimate or funded;
- establish qualification or skill fit;
- submit work;
- infer sponsor acceptance of *our* work;
- move funds;
- prove settlement, payout, availability of funds, or revenue.

Those remain separate gates. The purpose here is narrower and upstream:
**do not burn implementation effort or emit claim instructions for a canonical
issue whose own maintainer thread already says new work should stop.**
