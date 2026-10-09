# Canonical bounty viability gate

concierge.bounty_canonical_viability prevents external bounty-board rows from becoming build work until canonical GitHub state is still live, unsaturated, and compatible with the payment route.

It runs downstream of bounty_value_router. The value router answers whether the verified issue or milestone amount reaches the owner's active-work floor. This gate answers whether the listed work is still worth taking now.

## Why it exists

Bounty boards can lag canonical repositories. A row can remain open after the GitHub issue was closed as not planned, after the repository was archived, after another contributor was assigned, or after overlapping pull requests appeared. Starting from the board alone wastes swarm capacity and can create duplicate public work.

The gate requires fresh observations for listing state, repository archive state, issue state and acceptance, assignees, payment or selection route, open-PR overlap, and the whole collision/claim-pressure snapshot. Every output is deterministic, tamper-evident, and advisory-only.

## Dispositions

- PRUNE: canonical work is closed or archived, the provider/listing is closed, an open listing contradicts canonical closure, or the advertised route pays for reports rather than implementation.
- HOLD: evidence is stale, listing state is unknown, the feature is still an unaccepted proposal, another contributor owns it, scope is collision-heavy, or payment/selection evidence is insufficient.
- WAIT_ASSIGNMENT: assignment is required and this actor has already applied. Do not implement yet.
- READY_FOR_CLAIM_REVIEW: no blocker was found; review the claim or application route. This grants no claim authority.
- READY_FOR_IMPLEMENTATION_REVIEW: this actor is already assigned and no blocker was found. This still grants no write or submission authority.

## Collision semantics

An open pull request by another author with FULL or UNKNOWN overlap holds the row unless a maintainer explicitly confirms a separately payable residual. PARTIAL overlap is recorded but does not automatically block. Claim pressure at or above the configured threshold also holds unless a maintainer-confirmed residual exists.

A confirmed residual only overrides collision and pressure reasons. It never overrides closed or archived state, stale evidence, assignment requirements, or payment-route requirements.

## Value binding

Schema v2 embeds the earlier `bounty_value_router` receipt plus the selected work ID. The gate semantically verifies that upstream receipt, selects exactly one verified candidate by work ID, requires that candidate to be `VALUE_50_PLUS`, and requires its canonical source URL to identify the same GitHub owner/repo/issue as `canonical_issue_url`. The verified upstream receipt digest is carried into the downstream identity. This prevents a valid high-value receipt for candidate A from being replayed onto candidate B.

## Payment routes

- VERIFIED: an authoritative payout route exists for this work class; it does not mean an award was won or paid.
- UNVERIFIED: hold.
- SELECTION_GATED: hold until the actor is selected or assigned.
- REPORT_ONLY: prune from implementation work unless a separate implementation award exists.

## Live-clock replay fence

The **CLI** compares `evaluated_at` in the supplied snapshot to the runner's
actual UTC wall clock *before* compiling a receipt. It rejects an evaluation
more than five minutes old or more than 60 seconds future-dated. This prevents
an old canonical snapshot plus its old `evaluated_at` value from being replayed
today as if all source observations were fresh. All listing, repository, issue,
reward and collision observation ages are still assessed against the accepted
evaluation clock by the existing compiler.

The pure `compile_bounty_canonical_viability` function and `verify_receipt`
remain deterministic for historical review; they deliberately do not assert
the machine's current time. Any programmatic caller intending to dispatch live
paid work must apply the same live clock check, refresh the first-party sponsor
evidence, and satisfy the independent payer/active-repository policy. This
receipt remains advisory and never grants claim, build, submission or payout
authority.

Run with:

    python -m concierge.bounty_canonical_viability snapshot.json --json

Non-ready results exit 2 so orchestration can stop before it creates a claim, fork, branch, or implementation.
