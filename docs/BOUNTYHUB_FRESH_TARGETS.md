# Fresh BountyHub intake from retained evidence

Use this optional view when finding **new** work. It applies the existing catalog
reducer, then excludes qualifying **exclusive listings that already have an
assignee**. It never decides who that assignee is. Continue using the ordinary
`concierge bountyhub targets` command to recover existing assignments, including
our own.

```sh
python -m concierge.bountyhub_fresh_targets \
  work/supply/bountyhub/2026-10-04-catalog-56f3.json \
  --min-funded-usd 15.00 \
  --exclude-issues work/supply/bountyhub/2026-10-04-canonical-exclusions.json \
  --output fresh-targets.json \
  --preflight-output fresh-preflight-shortlist.json

python -m concierge.bounty_capture_batch fresh-preflight-shortlist.json \
  --output-dir fresh-canonical-capture --max-issues 25 --max-pages 10 \
  --max-requests 100 --json
```

The command makes **zero provider requests**. Input can be an existing
`bountyhub-catalog/v1` report or a `bountyhub-target-refresh/v1` report. It preserves
source observation times, incomplete coverage, funding policy, submission-target
mappings and canonical issue exclusions. The existing atomic output writer is
reused. `--submission-targets` and `--include-promised` are forwarded to the
ordinary reducer; promised money does not become funded money.

## What changes

`targets` and `listing_ids_by_issue` retain only the qualifying listing IDs not
excluded by this additional rule. When two listings refer to one issue, an
assigned exclusive listing does **not** suppress the other eligible listing.
Nonexclusive listings are unchanged. Missing or nonboolean assignment evidence
is an input error rather than evidence of availability.

Fresh-target output is explicitly **lead-only**. It carries
`dispatch_status="LEAD"`, `green_authorized=false`,
`requires_canonical_preflight=true`, and `requires_work_order_lease=true`.
The `canonical_preflight_candidates` field contains only the exact
`repo`/`number`/optional-`submission_target` shape accepted by
`bounty_capture_batch`. `--preflight-output` writes that envelope directly.
This removes the former manual field-stripping step; it does **not** turn a
retained marketplace row into GREEN work. Builders still consume only the later
live canonical/preflight result and the normal work-order lease.

Three additional fields make the filter inspectable:

- `fresh_intake_policy`: `exclude_assigned_exclusive_listings`
- `assigned_exclusive_exclusion_count`: number of qualifying listing IDs removed
- `assigned_exclusive_exclusions`: listing ID, repository, issue number and
  `exclusive_listing_already_assigned` reason for each removal

No assignee identity, pledge record, claim mutation or payout is produced. The
source report and ordinary reducer are unchanged. `source_complete` still means
source/funding coverage under the existing reducer, **not** current eligibility.
An output target still needs the usual current issue, owner, payer and submission
checks. This view is not a new assignment, authorization or payment gate.

## Motivating retained record

The October 4, 2026 07:00 UTC capture contains funded Freelens #1280 with
`assignment_type="exclusive"`, `has_assignee=true`, and `open_claim_count=0`.
Its normal shortlist includes that issue. This fresh-intake view excludes listing
`fe773082-b67f-4af3-a5e6-7011ddd18dcd` without pretending the old capture is fresh.
Source: `work/supply/bountyhub/2026-10-04-catalog-56f3.json`, blob
`ae74feb8aba24f826520026a3c7968185aabe44f`.

## Validation scope

Five focused offline checks exercised the production filter on the retained
assignment fields and synthetic mixed-listing, nonexclusive, partial-refresh and
malformed-assignment cases; verified input/timestamp preservation; checked
argument forwarding with a stub for the existing reducer; and ran real CLI help.
These checks made no network calls. They were not a full repository integration
run, live eligibility refresh, or payment proof.
