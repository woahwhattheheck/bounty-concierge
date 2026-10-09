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

Historical sponsor evidence requires a fresh owner registry (72h), exact canonical
repository with status QUALIFIED_ACTIVE_PAID, owner activity policy set to 30 days,
maintainer activity within 30 days, and a historical merged PR paired with a
first-party paid-expense/claim evidence source. Even a valid historical payer
record returns HOLD/EXACT_TASK_PREFLIGHT_REQUIRED for **new work** until the
separate owner's canonical exact repository + platform + task + claimant + amount
+ collection-rail preflight has been run and independently confirmed. This
cloud-side registry is not the authoritative local checker and never by itself
grants a new claim, build or unpaid revision.

This is an offline *curated registry* gate; it does not independently query
provider payout databases. A historical receipt does not prove present task acceptance,
payment, award, assignment, or eligibility of the current claimant.

Retain other issue, contributor policy, maintainer response, overlap, and
payment-route checks. For the same payer working in different repositories,
map independently verified historical payer evidence into the canonical new
repository's curated record; platform-wide reputation is not sufficient.
