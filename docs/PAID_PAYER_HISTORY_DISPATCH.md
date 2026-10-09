# Paid payer-history gate for new bounty work (2026-10-09)

This is a new implementation / new claim eligibility guard, not a claims
extinguishment process. Keep original PRs, branches, claim IDs, and settlement
follow-up intact when a repository is placed on HOLD.

**Operational input:** from an owner-curated, source-checked checkout, run:

    python -m concierge.bounty_qualification snapshots/canonical-issue.json \
      --payer-registry ../commons/ground/REPOSITORY_WORK_ELIGIBILITY.json --json

The registry path is explicit. Omitting it or supplying a stale or unmatched
record intentionally produces HOLD. An advertised reward, platform marked green,
escrowed listing, /claim, merged PR, or bounty card does not establish payment.

Positive eligibility needs a fresh owner registry (72h), the exact canonical
repository with status QUALIFIED_ACTIVE_PAID, maintainer activity within 90
days, and historical merged PR paired with a first-party paid-expense/claim
evidence source. Invalid or missing evidence produces HOLD.

This is an offline *curated registry* gate; it does not independently query
provider payout databases. Passing does not prove present task acceptance,
payment, award, assignment, or eligibility of the current claimant.

Retain other issue, contributor policy, maintainer response, overlap, and
payment-route checks. For the same payer working in different repositories,
map independently verified historical payer evidence into the canonical new
repository's curated record; platform-wide reputation is not sufficient.
