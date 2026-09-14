# Sponsor adjudication custody

`concierge.sponsor_adjudication` closes the evidence gap between **submitting bounty findings** and **proving cash was paid**.

The existing revenue settlement stack is intentionally downstream: it reconciles explicit payment evidence and prevents wallet transactions from being reused. It should not be forced to answer a different question: *what exactly did the sponsor say it was adjudicating?*

A sponsor can receive many reports, verify several concrete findings, and still say that the whole batch is one deduplicated claim set with its reward breakdown pending. This module preserves that statement without turning report count into claim count or verification into money.

## Authority boundary

This module is evidence custody only. It cannot:

- contact a sponsor;
- submit or re-submit a bounty claim;
- mutate a provider, wallet, or bank;
- initiate a payout;
- prove a sponsor owes money;
- recognize payment; or
- recognize accounting revenue.

Every report and receipt carries those authority flags as `false` and verification rejects escalation.

## Input model

The strict JSON manifest has four parts:

1. `program`: stable program identity, sponsor display name, and program source reference.
2. `findings`: immutable finding IDs, SHA-256 fingerprints, titles, submitter labels, and retained evidence refs.
3. `submissions`: exact submission receipts with one or more finding IDs and canonical UTC submission time.
4. `sponsor_events`: retained sponsor-origin evidence with unique event IDs, evidence digests, canonical UTC time, and sponsor claim-unit identity.

Supported sponsor event types:

- `SPONSOR_RECEIVED`
- `SPONSOR_VERIFIED`
- `ADJUDICATION_STARTED`
- `REWARD_OFFERED`
- `DUPLICATE_COLLAPSED`
- `DECLINED`
- `PAYMENT_REPORTED`

`PAYMENT_REPORTED` is deliberately **not** cash evidence. It advances only to `paid_evidence_pending`, which is a prompt for independent settlement evidence.

## The key separation

Findings, submissions, and sponsor claim units are separate identities.

A sponsor may bind three findings from two separate emails to one claim unit. The output keeps all three finding fingerprints and both submission receipts, while the sponsor-unit count remains one. A later attempt to silently bind one finding to a second active unit fails closed; an explicit later `DUPLICATE_COLLAPSED` event is required to merge already-created units.

A collapse is refused after a source unit already has reward authority. That forces ambiguous money-bearing dedupe changes into manual review instead of silently moving value between units.

## States and actions

Claim units use only:

- `submitted`
- `sponsor_verified`
- `adjudicating`
- `reward_offered`
- `declined`
- `duplicate`
- `paid_evidence_pending`

The deterministic action vocabulary is:

- `WAIT_SPONSOR`: sponsor has received/verified/adjudicating evidence; do not create another outbound touch.
- `DO_NOT_RESUBMIT`: sponsor authority already covers the finding, or a unit is declined/duplicate.
- `OWNER_REVIEW`: no sponsor response yet, a reward needs human review, or a payment report still needs independent cash evidence.

Finding-level output becomes `DO_NOT_RESUBMIT` as soon as sponsor-received evidence covers it. This is intentionally stronger than the unit-level `WAIT_SPONSOR` label: the unit may be waiting, while each already-covered finding should not be filed again.

## Fail-closed rules

The compiler rejects, among other cases:

- duplicate JSON keys, floats, `NaN`, and `Infinity`;
- unknown schema fields;
- duplicate IDs or two finding IDs sharing one fingerprint;
- sponsor events predating referenced submissions;
- ambiguous same-time ordering inside one sponsor unit;
- silent finding rebinding to a second active unit;
- collapse into self, collapse of missing/terminal units, or collapse after a reward offer;
- reward/payment amount mutation;
- payment reports without an exact prior reward offer;
- events after a terminal unit state; and
- authority-flag tampering in reports/receipts.

The output does not infer cash under any state.

## Deterministic outputs

Compile:

```bash
python -m concierge.sponsor_adjudication compile manifest.json --out-dir ./adjudication-out
```

The compiler writes:

- `adjudication.json` — canonical machine report with `report_sha256`;
- `claim_units.csv` — spreadsheet-safe unit table;
- `adjudication.md` — human review packet; and
- `receipt.json` — manifest/report/file digest binding plus all-false authority ceiling.

Verify later, offline:

```bash
python -m concierge.sponsor_adjudication verify ./adjudication-out
```

An exact manifest recompiles byte-for-byte. The verifier re-hashes every emitted file and verifies report/receipt authority ceilings.

## Example: verified findings, one deduped set, reward pending

A sponsor response saying “the concrete findings are verified; we are adjudicating one deduplicated set; reward breakdown pending” should be represented with one `SPONSOR_VERIFIED` event binding every covered finding to one claim unit followed by `ADJUDICATION_STARTED`.

The result is:

- one sponsor claim unit, not N report-count claims;
- `status=adjudicating`;
- `sponsor_verified=true`;
- unit action `WAIT_SPONSOR`;
- finding action `DO_NOT_RESUBMIT`; and
- `cash_status=not_inferred` everywhere.

That is exactly the intended boundary: preserve sponsor truth, stop duplicate outbound work, and wait for the sponsor without minting a reward or payment claim.
