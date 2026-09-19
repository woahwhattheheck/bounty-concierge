# GrantFox queue gate

`concierge.grantfox_queue_gate` turns a **fresh, read-only observation** of one
GrantFox issue page into a deterministic queue receipt before a worker applies
or starts assignment-dependent implementation.

The gate exists to prevent three recurring failure modes in a fast swarm:

1. treating `Maybe Rewarded` or campaign labels as a fixed award;
2. starting a duplicate build when GrantFox already links a pull request; and
3. piling applications onto an already-assigned or saturated issue.

It is intentionally advisory-only. A receipt never posts the GrantFox
application, comments on GitHub, creates a branch, submits a PR, changes a
wallet, or proves an award/payment.

## Snapshot contract

```json
{
  "schema": "grantfox-queue-gate/v1",
  "listing_url": "https://contribute.grantfox.xyz/org/Gryd-lock/repo/grydlock-testkit/issue/33",
  "canonical_issue_url": "https://github.com/Gryd-lock/grydlock-testkit/issues/33",
  "issue_state": "open",
  "actor_login": "woahwhattheheck",
  "assigned_to": null,
  "actor_applied": false,
  "application_count": 1,
  "application_pressure_threshold": 3,
  "linked_pr_urls": [],
  "labels": ["Maybe Rewarded", "GrantFox OSS", "Official Campaign | FWC26"],
  "observed_at": "2026-09-19T21:36:00Z",
  "evaluated_at": "2026-09-19T21:38:00Z",
  "max_snapshot_age_seconds": 900
}
```

`application_count` is the count observed on the provider page. Callers should
not invent it from a stale Slack mirror. `actor_applied` and `assigned_to` must
also come from the current provider/GitHub state.

Run:

```bash
python -m concierge.grantfox_queue_gate snapshot.json --json
```

## Dispositions

- `APPLY_ELIGIBLE`: the issue is open, fresh, unassigned, below the configured
  application-pressure threshold, and has no linked PR. The receipt only says
  that the provider application route is the next logical step; it does not
  perform or authorize that step.
- `WAIT_ASSIGNMENT`: this actor already applied and the issue remains
  unassigned. Do not duplicate the application.
- `IMPLEMENTATION_ELIGIBLE`: the provider snapshot assigns the issue to this
  actor and no higher-priority hold exists. The receipt still grants no write
  authority by itself.
- `HOLD`: stop and refresh/reuse existing work. Reason codes identify a closed
  issue, stale snapshot, linked PR, assignment to someone else, or high
  application pressure.

A `Maybe Rewarded` label maps only to `POSSIBLE_DISCRETIONARY`.
`explicit_amount_verified`, `award_verified`, and `payment_verified` remain
false. Amount/award/payment truth belongs to separately verified provider
evidence and settlement tooling.

## Identity and freshness fences

The GrantFox URL and GitHub issue URL must resolve to the exact same
case-insensitive owner/repository and positive issue number. URLs with userinfo,
explicit ports, query/fragment data, encoded path aliases, repeated separators,
or non-HTTPS schemes fail closed.

Freshness is deterministic: the input includes both `observed_at` and
`evaluated_at`. This keeps receipts reproducible instead of embedding wall-clock
state. A receipt is SHA-256 bound and `verify_receipt()` rejects mutation.

## Authority ceiling

Every receipt explicitly sets all provider application, implementation write,
submission, and payment/wallet authority to `false`. Use the receipt for queue
coordination; use the relevant authenticated provider/GitHub workflow for any
actual mutation.
