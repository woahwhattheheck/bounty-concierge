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

## HTTP connection reuse

The default availability check owns one `requests.Session` for its issue reads
and both paginated comment traversals. It closes that session when the check
returns or raises, including an early HOLD for a closed issue. Argument
validation runs before the session is allocated. Callers that supply a session
keep ownership and can reuse it for later checks.

Complete checks still perform the same live reads and generation comparisons.
For 101 comments across two pages, six GETs share one connection pool instead of
creating six separate pools. There is no added cache, retry, or delay.

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

If the first comment traversal reaches `max_pages` without a short terminal
page, the guard immediately returns `HOLD / COMMENT_HISTORY_TRUNCATED` with
`dispatch=false`. It does not repeat that already-incomplete traversal or
request a final issue snapshot. No later response could make the first
traversal complete, and a later HTTP error must not erase this known result.
The receipt's `issue_state` is the initial validated observation, not a claim
that the issue state was reread after truncation. Signal evidence stays empty;
partial comments are not treated as complete availability or payout evidence.

With `max_pages=10` and ten full pages, this reduces one check from 22 GETs to
11. With `max_pages=1` and one full page, it reduces four GETs to two. Exactly
100 comments at a one-page bound still produce HOLD, not CLEAR: the missing
terminal page is not inferred from the issue's declared comment count.
Complete histories, second-traversal truncation, generation comparisons,
comment validation, closed-issue handling and session ownership are unchanged.
These counts describe the request path, not a measurement of live provider
latency or cash saved.

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
