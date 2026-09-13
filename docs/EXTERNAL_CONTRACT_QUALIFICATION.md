# External paid-contract qualification authority

`concierge.contract_qualification` is the fail-closed boundary for paid work that does **not** have a canonical GitHub bounty issue.

It exists because an open marketplace listing is not bid authority. A listing can require specific experience, a current human handoff, portfolio proof, minimum balance, KYC, or other screening facts that are not established by a generic skill match. The gate refuses to turn "we could do this" into "we have done this."

This module validates already-collected trusted evidence. It does not browse a marketplace and never submits a bid, accepts terms, creates an account, satisfies KYC, spends money, accepts a contract, claims an award, receives payment, or recognizes revenue.

## Input boundary

The input schema is `bounty-concierge.external-contract-qualification/v1`.

### `listing`

The trusted listing observation contains:

- canonical HTTPS `url`;
- `state`: `OPEN`, `CLOSED`, or `UNKNOWN`;
- canonical UTC `observed_at`;
- optional canonical UTC `closes_at`;
- SHA-256 `source_digest` of the exact normalized source observation;
- native `currency`;
- exact `min_amount` and `max_amount`.

An OPEN observation older than 30 minutes is HOLD. `CLOSED` or a passed deadline is REJECT. Native currencies are preserved; this gate never invents an FX conversion.

The normalized amount range is authoritative for the bid. If a marketplace header and the buyer's body disagree, the upstream collector must resolve that conflict before qualification. The gate will not infer a wider range.

### `required_claims`

Each screening claim has:

- stable `claim_id`;
- `claim_type`: `EXPERIENCE`, `TOOL`, `AVAILABILITY`, `PORTFOLIO`, `RATE`, or `OTHER`;
- `mandatory`.

Raw buyer prompt text is intentionally unnecessary. Receipts expose claim IDs, not source prompt bodies.

### `evidence`

Each evidence row binds exactly one claim:

- stable `evidence_id`;
- `claim_id`;
- evidence type;
- immutable/reference locator;
- SHA-256 evidence digest;
- canonical UTC observation time;
- `scope`: `EXACT` or `ADJACENT`.

`ADJACENT` evidence is never promoted to exact experience. For example, generic HTTP/API work cannot satisfy a mandatory Microsoft Graph or ServiceM8 claim. Availability evidence expires after 24 hours.

Allowed evidence types are deliberately claim-specific:

| Claim | Accepted exact evidence |
| --- | --- |
| EXPERIENCE | merged PR, repository commit, provider receipt, owner attestation |
| TOOL | merged PR, repository commit, provider receipt, owner attestation |
| AVAILABILITY | current-availability evidence, owner attestation |
| PORTFOLIO | portfolio artifact, merged PR, repository commit |
| RATE | owner attestation |
| OTHER | any supported evidence type |

Owner attestation is explicit authority; the gate does not synthesize it from adjacent repo activity or old sales copy. Extra adjacent or incompatible evidence does not poison a claim that also has valid exact evidence; it only explains a mandatory claim when that claim otherwise remains unproven.

### `platform`

Platform readiness is separately bound:

- route: `MANUAL_OWNER` or `CONNECTED_ACTION`;
- whether requirements are complete;
- whether fees are known;
- whether the account is ready;
- required and available native balance;
- KYC state: `NOT_REQUIRED`, `SATISFIED`, `UNSATISFIED`, or `UNKNOWN`.

Unknown fees/KYC, incomplete requirements, an unready account, or insufficient balance HOLDs qualification. The gate does not take any of those actions.

### `bid`

The proposed bid carries native `currency`, exact `amount`, and positive `delivery_days`. Currency mismatch or an amount outside the normalized listing range HOLDs.

## Decision

The receipt is content-addressed over every decision-bearing normalized field.

- `ACTIONABLE` + manual route -> `READY_FOR_OWNER_SUBMISSION`
- `ACTIONABLE` + connected route -> `READY_FOR_CONNECTED_SUBMISSION`
- unresolved evidence/platform/source gates -> `HOLD`
- closed/past-deadline work -> `REJECT`

`submitted` is always `false`. Even a ready connected route only means the evidence supports a later explicit submission action.

Every operational authority flag stays false, including marketplace submission, terms acceptance, account creation, KYC completion, spend, contract acceptance, award, payment receipt, and revenue recognition.

## Verification

`verify_contract_qualification_receipt(snapshot, receipt, as_of=...)` first rebuilds the receipt at its bound evaluation time, then re-evaluates the same evidence at a trusted current time. Any source digest, claim, evidence digest, bid, platform state, or other decision-bearing change invalidates the old receipt. Verification time cannot precede qualification time.

A historically valid ACTIONABLE receipt is not current authority by itself. If the bound listing observation or availability evidence has gone stale by the trusted current time, verification fails closed and a fresh marketplace observation/evidence set must be qualified.

## CLI

```bash
python -m concierge.contract_qualification snapshot.json \
  --as-of 2026-09-13T10:30:00Z --json
```

Exit status:

- `0` ACTIONABLE;
- `2` HOLD;
- `3` REJECT;
- argparse input failure for malformed/untrusted input.

## Why this is separate from GitHub bounty intake

`bounty_qualification` and `revenue_intake` own canonical GitHub bounty reward, competition, maintainer, and issue-state authority. `opportunity_ranker` is downstream and assumes eligibility is already established.

External marketplaces have a different trust problem: screening questions, portfolio/experience assertions, manual owner clicks, marketplace account state, and native platform costs. This module handles that boundary without weakening the existing bounty path or pretending a read-only listing is a submission connector.
