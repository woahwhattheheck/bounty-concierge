# First-party portal-registration gap check

`concierge.portal_registration_reconcile.reconcile(github, portal)` is a small, **read-only** projection for the existing reward/collection ledger. It detects when a Github PR has been delivered but its exact PR URL is not listed as submitted to the funding platform. It does not add a queue, scheduler, account, payout action, or source-build gate.

## Captured input

Supply one cached Github observation:

```json
{"repo":"egoist/majo","issue":9,"pr":59,"head_sha":"0000000000000000000000000000000000000000","author":"woahwhattheheck","claimant":"woahwhattheheck","linked_issue":true,"relation":"continuation"}
```

The SHA above is a structural example **only**, not an actual current PR head.

Supply one independently captured first-party portal observation with keys `provider` (`issuehunt` or `bountyhub`), `source_url`, `captured_at` (UTC seconds), `http_status`, `complete`, `registered_pr_urls` (the **entire** issue-specific output list, containing full GitHub PR URLs), and `solver_claims` (provider claim rows for PR/claimant, if any). Do not substitute the GitHub issue's comment list or the sponsor's generic creator-payment status for the portal list.

Calls return one stable key `portal:platform:owner/repo#issue:claimant:prN` and deterministic `work_item_id`, allowing a Slack publisher to deduplicate notices across agents and unchanged PR heads. A missing-registration notice is emitted only when the first-party snapshot is complete, HTTP 200, no older than 15 minutes, the GitHub PR is issue-linked, and the original author matches the requested claimant. An unknown contribution lineage fails closed. Different PR numbers **never** share registration, even when an earlier original contributor's PR is already listed.

## States and handoff

- `GITHUB_SUBMITTED / PORTAL_NOT_REGISTERED`: actionable; send **one** internal item with its stable work-item ID to the existing authenticated platform operator, who must independently check eligibility before registering the exact PR.
- `PORTAL_REGISTERED / UNAWARDED`: listed but no positive claimant-specific award evidence.
- `AWARDED` and `PAID`: returned only when the captured solver-specific claim row includes the respective explicit provider receipt. These are unverified **provider reports** in this projection, **not** cash/ledger or bank settlement verification.
- `UNKNOWN`: stale, incomplete, unmatched-author, or insufficient provenance. Never silently interpret it as zero claims, nonpayment, or eligibility.

All results retain `authority.provider_write=false`, `award_verified=false`, and `paid_verified=false`. Only `concierge.reward_settlement_ledger` plus independent authenticated settlement evidence may certify a financial outcome. The projection makes **zero** API calls and consumes externally captured/cached observations, avoiding extra GitHub searches while the fleet is rate limited.

Examples observed in the current collection queue: egoist/majo PR #59 and egoist/bili PRs #640, #641 and #642 are separate follow-on submissions from previously registered original authors. Their registration cannot be inferred from those earlier authors' portal entries. Do not submit or contact an original contributor automatically.
