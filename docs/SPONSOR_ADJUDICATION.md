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

### Sponsor-origin evidence is host-authorized, not caller-asserted

A manifest's `source_ref` and `source_sha256` fields are **not** authority by themselves. For every non-empty `sponsor_events` generation, the credential-owning host must first reacquire the sponsor evidence, then attach a fresh `sponsor_authority` record. Compilation fails closed when that record is absent, stale, future-dated, signed by the wrong host identity, transplanted to another event generation, or transplanted to another manifest generation.

The host authority binds:

- purpose `bounty-sponsor-adjudication-event-authority/v1`;
- an authorized provider ID;
- an authorized principal SHA-256 (never a raw mailbox/token in the report);
- second-precision UTC `captured_at` with a 300-second compile freshness window;
- `event_scope_sha256`, covering program identity plus the complete normalized sponsor-event generation;
- `manifest_sha256`, covering the entire authority-free manifest generation; and
- `signature_sha256`, HMAC-SHA256 over the preceding authority record.

Host configuration is environment-owned, not accepted as a caller argument:

```text
BOUNTY_SPONSOR_ADJUDICATION_HMAC_KEY_HEX
BOUNTY_SPONSOR_ADJUDICATION_AUTHORIZED_PROVIDER
BOUNTY_SPONSOR_ADJUDICATION_AUTHORIZED_PRINCIPAL_SHA256
```

The HMAC key must be at least 32 bytes. A trusted adapter may use `authority.sign_for_test_or_host_fixture(...)` **only after the credential-owning host has reacquired the evidence**. The helper proves possession of host configuration; it does not perform provider acquisition itself.

The former `BOUNTY_SPONSOR_ADJUDICATION_TEST_ONLY_ALLOW_UNSIGNED` switch is retired. Its presence now fails closed on compile, report binding, retained verification, and fixture signing; production has no unsigned sponsor-authority mode. The predecessor semantic regression suite is preserved by test-only code outside the `concierge` package: `tests/sponsor_adjudication_unsigned_site/sitecustomize.py` is loaded only when a dedicated `unittest` step or child CLI process explicitly prepends that exact directory to `PYTHONPATH`, while `tests/conftest.py` applies the same compatibility functions only to the two named predecessor modules during repository-wide `pytest` runs and restores every binding afterward. Neither mechanism is activated by a production environment flag or ordinary package import.

The retained authority record is embedded under `report.program.sponsor_authority`; the report digest and receipt file digests therefore bind it durably. Public `verify_report` / `verify_artifacts` verify the HMAC and exact retained event scope historically. Historical verification deliberately does not reapply the 300-second freshness window: freshness is an ingestion property, while signature/event-scope integrity must remain verifiable later.

## Input model

The strict JSON manifest has five logical parts:

1. `program`: stable program identity, sponsor display name, and program source reference.
2. `findings`: immutable finding IDs, SHA-256 fingerprints, titles, submitter labels, and retained evidence refs.
3. `submissions`: exact submission receipts with one or more finding IDs and canonical UTC submission time.
4. `sponsor_events`: retained sponsor-origin evidence with unique event IDs, evidence digests, canonical UTC time, and sponsor claim-unit identity.
5. `sponsor_authority`: required only when `sponsor_events` is non-empty; host HMAC authority for that exact current generation.

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

A collapse is refused if either the source unit or target unit already has reward authority. That forces ambiguous money-bearing dedupe changes into manual review instead of silently changing which findings an offered amount appears to cover.

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

- `WAIT_SPONSOR`: host-authorized sponsor evidence says received/verified/adjudicating; do not create another outbound touch.
- `DO_NOT_RESUBMIT`: host-authorized sponsor authority already covers the finding, or a unit is declined/duplicate.
- `OWNER_REVIEW`: no sponsor response yet, a reward needs human review, or a payment report still needs independent cash evidence.

Finding-level output becomes `DO_NOT_RESUBMIT` as soon as host-authorized sponsor-received evidence covers it. This is intentionally stronger than the unit-level `WAIT_SPONSOR` label: the unit may be waiting, while each already-covered finding should not be filed again.

## Fail-closed rules

The compiler rejects, among other cases:

- non-empty sponsor events without current host authority;
- a configured retired unsigned-authority environment variable;
- wrong provider/principal, invalid HMAC, stale/future capture, or authority replay/transplant;
- event-scope or full manifest mutation after host attestation;
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

Compile after the trusted host has attached `sponsor_authority`:

```bash
python -m concierge.sponsor_adjudication compile manifest.json --out-dir ./adjudication-out
```

The compiler writes:

- `adjudication.json` — canonical machine report with `report_sha256` and retained sponsor authority;
- `claim_units.csv` — spreadsheet-safe unit table;
- `adjudication.md` — human review packet; and
- `receipt.json` — manifest/report/file digest binding plus all-false authority ceiling.

Verify later:

```bash
python -m concierge.sponsor_adjudication verify ./adjudication-out
```

The public verifier re-hashes every emitted file, verifies report/receipt authority ceilings, verifies the retained host HMAC, and rebinds that HMAC to the exact sponsor-event scope carried in the report. It is a historical verification; it does not pretend to reacquire a provider message at verification time.

## Example: verified findings, one deduped set, reward pending

A sponsor response saying “the concrete findings are verified; we are adjudicating one deduplicated set; reward breakdown pending” should be represented with one `SPONSOR_VERIFIED` event binding every covered finding to one claim unit followed by `ADJUDICATION_STARTED`, then host-authorized only after that evidence has been reacquired at the trusted provider boundary.

The result is:

- one sponsor claim unit, not N report-count claims;
- `status=adjudicating`;
- `sponsor_verified=true`;
- unit action `WAIT_SPONSOR`;
- finding action `DO_NOT_RESUBMIT`; and
- `cash_status=not_inferred` everywhere.

That is exactly the intended boundary: preserve sponsor truth, stop duplicate outbound work, and wait for the sponsor without letting caller-authored JSON mint a reward or payment claim.
