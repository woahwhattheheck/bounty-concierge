# Vendor Patch Pursuit Authority

`concierge.vendor_patch_pursuit` is the fail-closed owner-review carrier for a
specific class of paid security/open-source work:

> the upstream GitHub issue does **not** advertise a bounty, but a separate
> vendor-operated patch program may pay for an eligible security improvement.

This is intentionally separate from `bounty_qualification`. The ordinary
bounty gate is correct to hold an issue when the issue itself does not advertise
a reward. A vendor program cannot be represented by weakening that gate because
the reward evidence, target scope, contribution rules, and freshness authority
live in a different source.

The carrier therefore composes three independently evidenced facts:

1. **Candidate state** — exact repository + issue, current issue state,
   assignment occupancy, current matching-PR occupancy, and proof that the PR
   search completed.
2. **Program state** — official HTTPS program source, exact repository scope,
   active/paused/closed state, program reward schedule when the source actually
   supplies one, and contribution route.
3. **Fleet reservation state** — exact `vendor-patch:owner/repo#issue` key,
   current owner, collision count, expiry, and content-addressed reservation
   evidence.

Only when all three are current and clean does the packet return
`PURSUE_FOR_OWNER_REVIEW`.

## What PURSUE means

`PURSUE_FOR_OWNER_REVIEW` means only:

- current evidence identifies an open, unassigned upstream issue;
- a complete current search reports no matching open PR;
- an active vendor program source currently places that exact repository in
  scope;
- the vendor source itself evidences a non-zero reward schedule;
- one live fleet reservation owns the exact issue key with zero collisions.

It does **not** mean:

- the candidate will receive a reward;
- a particular reward tier or multiplier applies;
- a patch has been accepted;
- the vendor has approved eligibility;
- outreach, claim submission, terms acceptance, or spending is authorized;
- payment/cash/revenue exists.

The reward range in the packet is labelled **program schedule only**. It is
never treated as candidate-specific expected value or an award.

## Why separate program evidence is required

The legacy/direct bounty path looks for high-precision reward signals in the
canonical issue itself. That is useful for issue bounties and should stay
fail-closed.

Vendor patch programs reverse the evidence topology:

```text
upstream issue              official vendor program
-------------               -----------------------
bug / hardening target      target repo is in scope
usually no "$ bounty"       program reward schedule
assignment + PR occupancy   contribution / claim route
         \                   /
          \                 /
           vendor_patch_pursuit
                    |
          PURSUE_FOR_OWNER_REVIEW
                    |
              owner decision
                    |
          existing payoff-path gate
```

The new carrier never injects program-wide reward text into the ordinary issue
gate. Each authority keeps its original meaning.

## Input schema

Top-level schema:

```json
{
  "schema": "bounty-concierge.vendor-patch-pursuit/v1",
  "opportunity_id": "stable-log-safe-id",
  "operator_id": "fleet-seat-id",
  "candidate": {},
  "program": {},
  "reservation": {}
}
```

Unknown keys fail closed.

### Candidate

Exact fields:

- `repository` — `owner/repository`.
- `issue_number` — positive integer.
- `issue_url` — exact fragment-free `https://github.com/owner/repo/issues/N`.
- `issue_state` — `OPEN`, `CLOSED`, or `UNKNOWN`.
- `assignee_count` — current canonical count.
- `open_pr_count` — count from the matching-PR occupancy search.
- `pr_search_complete` — must be `true`; truncation/incomplete search holds.
- `observed_at` — canonical second-precision UTC.
- `evidence_ref` — fragment-free HTTPS source reference.
- `evidence_sha256` — lowercase SHA-256 for the exact evidence bytes.

Candidate observations age out after 30 minutes. A reviewable candidate must be
open, unassigned, have zero matching open PRs, and have a complete PR search.

### Program

Exact fields:

- `program_id`
- `program_name`
- `source_url`
- `status` — `ACTIVE`, `PAUSED`, `CLOSED`, or `UNKNOWN`
- `repository_in_scope` — explicit source-derived boolean
- `scope_repository` — must exactly equal the candidate repository
- `reward_state` — `PROGRAM_SCHEDULE` or `UNSPECIFIED_BY_SOURCE`
- `currency`
- `min_reward_minor`
- `max_reward_minor`
- `contribution_route`
- `observed_at`
- `evidence_ref`
- `evidence_sha256`

Allowed contribution routes are:

- `MERGED_PR_THEN_CLAIM`
- `PATCH_THEN_PROGRAM_REVIEW`
- `PROGRAM_DEFINED`

Program evidence ages out after seven days. This forces periodic re-checking of
vendor rules instead of treating a once-seen program page as permanent policy.

`PROGRAM_SCHEDULE` requires an uppercase three-letter currency and a positive
minor-unit range. `UNSPECIFIED_BY_SOURCE` requires all amount fields to be
`null`. This prevents a scout from attaching an invented dollar value to a
program that did not publish one.

### Reservation

Exact fields:

- `reservation_key`
- `owner_id`
- `state` — `ACTIVE`, `RELEASED`, or `UNKNOWN`
- `collision_count`
- `observed_at`
- `expires_at`
- `evidence_ref`
- `evidence_sha256`

The key is not caller-selectable in meaning. It must equal:

```text
vendor-patch:<candidate repository>#<candidate issue number>
```

The reservation owner must equal top-level `operator_id`, the state must be
`ACTIVE`, collision count must be zero, and the observation must be at most 30
minutes old. Reservations may span at most 12 hours and must still be unexpired
when evaluated.

This is the cross-seat anti-duplication boundary. It is independent of upstream
GitHub assignment because fleet custody and upstream maintainer assignment
answer different questions.

## Dispositions and reason codes

The carrier returns exactly two dispositions:

- `PURSUE_FOR_OWNER_REVIEW`
- `HOLD`

Representative hold reasons:

| Reason | Meaning |
|---|---|
| `CANDIDATE_ISSUE_NOT_OPEN` | upstream target is closed/unknown |
| `CANDIDATE_ASSIGNED` | at least one upstream assignee exists |
| `CANDIDATE_PR_OCCUPIED` | a matching open PR already exists |
| `CANDIDATE_PR_SEARCH_INCOMPLETE` | occupancy search was not authoritative |
| `CANDIDATE_OBSERVATION_STALE` | issue/PR observation is older than 30 minutes |
| `PROGRAM_NOT_ACTIVE` | program is not currently active |
| `PROGRAM_REPOSITORY_OUT_OF_SCOPE` | exact repo is not evidenced in scope |
| `PROGRAM_REWARD_NOT_EVIDENCED` | source did not establish a paid schedule |
| `PROGRAM_OBSERVATION_STALE` | program source is older than seven days |
| `RESERVATION_OWNER_MISMATCH` | another seat owns the reservation |
| `RESERVATION_NOT_ACTIVE` | local reservation is not live |
| `RESERVATION_COLLISION` | more than one seat is contending |
| `RESERVATION_OBSERVATION_STALE` | collision evidence is too old |
| `RESERVATION_EXPIRED` | lease elapsed |
| `RESERVATION_KEY_MISMATCH` | lease is for a different upstream issue |

Multiple reasons are preserved, de-duplicated, and sorted so one failing check
cannot hide another.

## Receipt and verification model

`compile_pursuit(...)` emits:

1. deterministic JSON review packet;
2. deterministic Markdown review packet;
3. a receipt containing SHA-256 of the normalized input, JSON packet, and exact
   Markdown bytes.

`verify_pursuit(...)`:

- reconstructs the original packet at the receipt evaluation time;
- rejects packet, Markdown, or receipt tampering;
- rejects verifier time that predates the receipt;
- re-evaluates the evidence at verifier current time;
- rejects a previously reviewable receipt once any freshness/lease condition
  has expired.

A `HOLD` receipt remains verifiable as historical evidence; it does not become
permission to proceed.

## Payoff-path handoff

The packet includes a narrow `payoff_path_handoff`:

```json
{
  "mechanism": "BOUNTY",
  "source_url": "...",
  "source_evidence_sha256": "...",
  "next_conversion_event": "SUBMIT_WORK",
  "reward": {
    "state": "PROGRAM_SCHEDULE",
    "currency": "USD",
    "min_reward_minor": 50000,
    "max_reward_minor": 1500000
  }
}
```

This is **input material** for the existing payoff-path/free-work authority, not
a replacement for it. The existing gate should still enforce owner budget and
unpaid-exposure continuity.

## CLI

Compilation uses process UTC as authoritative current time:

```bash
python -m concierge.vendor_patch_pursuit compile \
  --input candidate.json \
  --packet review.packet.json \
  --markdown review.md \
  --receipt review.receipt.json
```

Verification:

```bash
python -m concierge.vendor_patch_pursuit verify \
  --input candidate.json \
  --packet review.packet.json \
  --markdown review.md \
  --receipt review.receipt.json
```

Output files are create-exclusive. Existing files are not silently overwritten.
There is deliberately no CLI `--as-of` argument. Library tests may provide a
trusted deterministic `now_utc`; production CLI evaluation uses verifier-owned
process time.

## Evidence collection boundary

This module is offline. It does not browse GitHub, Slack, or a vendor program
page. Callers must collect those observations through separately authorized,
canonical readers and provide exact evidence references/digests.

That separation is intentional: a decision compiler should not silently expand
its authority into source scanning, outreach, exploitation, submissions, or
payment operations.

## Security / commercial authority ceiling

The carrier must never be interpreted as authorization to:

- probe a live service or exploit a vulnerability;
- contact a vendor, maintainer, or prospect;
- claim an upstream issue;
- open or merge an upstream PR;
- accept contributor or program terms;
- submit a reward claim;
- spend money or deploy infrastructure;
- assert a bounty award, payment, cash receipt, or recognized revenue.

It answers one narrower question: **is there enough current, content-addressed,
collision-safe evidence to put this vendor-funded patch opportunity in front of
the owner for a pursuit decision?**
