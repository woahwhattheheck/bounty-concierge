# BountyHub canonical paid-build admission (offline)

`concierge/bountyhub_canonical_admission.py` converts an already collected BountyHub listing plus first-party GitHub and sponsor payment receipts into a bounded READY/HOLD recommendation for **new** engineering. It does not fetch websites, authenticate, register, claim, publish a PR, award money or prove anyone personally got paid.

The live BountyHub card is not the canonical issue. Record the exact first-party GitHub issue status and assignees, all matching *open* PRs and whether the PR census is complete, repository archived status, observed time, and most recent maintainer action. The payer section requires an operator-verified completed same-sponsor paid-merge receipt from Open Collective or Algora. The CLI validates receipt URL form but cannot verify its contents offline; independent first-party verification is mandatory before setting that flag.

Input shape:

```json
{
  "schema": "bountyhub-canonical-admission/v1",
  "as_of": "2026-10-09T08:30:00Z",
  "listing": {
    "id": "example-listing",
    "issue_url": "https://github.com/example/repo/issues/42",
    "advertised_usd": "75.00",
    "funding_type": "escrowed"
  },
  "github": {
    "observed_at": "2026-10-09T08:20:00Z",
    "issue_url": "https://github.com/example/repo/issues/42",
    "state": "open",
    "repository_archived": false,
    "last_maintainer_action_at": "2026-10-08T10:00:00Z",
    "assignees": [],
    "linked_open_pr_urls": [],
    "linked_pr_census_complete": true
  },
  "payer": {
    "sponsor_name": "Example",
    "proof_sponsor_name": "Example",
    "completed_paid_merge_verified": true,
    "receipt_url": "https://opencollective.com/example/expenses/123"
  }
}
```

Run from the repository root:

```sh
python3 -m concierge.bountyhub_canonical_admission candidate.json --output decision.json
PYTHONPATH=. python3 tests/test_bountyhub_canonical_admission.py
```

Reasons include: closed or mismatched issue, source older than 24h, maintainer idle >90 days, archive, assigned contributor, existing open solution, incomplete linked-PR census, unsuitable/unknown payout evidence or funding, and amounts under the $15 owner floor.

`READY_FOR_NEW_BUILD` is **not** an assignment, claim, sponsor acceptance, or payment entitlement. Promised and escrowed cards remain distinguishable; review and claim using the original GitHub account after satisfying all separate marketplace terms. Never convert a previously authored/claimed PR into a new contribution.
