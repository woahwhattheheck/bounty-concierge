# Forecast calibration for paid work

The concierge already qualifies paid work, ranks it, and solves bounded portfolios exactly. `forecast_calibration.py` adds the missing feedback loop: it uses **resolved historical outcomes** to calibrate the operator estimates that feed those optimizers.

This is an estimation system, not an authority system. It never establishes that an opportunity is legitimate, available, unclaimed, funded, awarded, settled, paid, or revenue.

## Inputs

Calibration consumes two strict JSON documents plus a trusted cutoff time.

### Historical outcomes

Schema: `opportunity-forecast-history/v1`

Each record carries:

- immutable `record_id` and `candidate_id`;
- explicit `segment` chosen by the operator (for example `external-contract` or `bug-bounty`);
- the probability and effort estimate that existed **before** the outcome;
- `outcome`: `won`, `lost`, or `unresolved`;
- actual effort and canonical millisecond UTC `closed_at` for resolved work;
- a SHA-256 digest of the evidence/source snapshot.

Resolved rows after the trusted cutoff are rejected. Unresolved rows cannot carry terminal timestamps or actual effort and are never silently treated as losses. Exact duplicate record replays collapse; a changed replay fails. A second record ID for the same historical candidate also fails so repeated bookkeeping cannot inflate sample size.

### Current candidates

Schema: `opportunity-forecast-candidates/v1`

Each candidate carries an immutable candidate ID, canonical source URL, explicit segment, original probability/effort estimates, and source SHA-256. Candidate IDs must be unique.

## Calibration math

For each segment, the engine computes resolved sample count, wins, historical Brier score, and realized/estimated effort totals.

Probability calibration is intentionally conservative. Once `minimum_samples` is met, the historical win rate receives a Beta(1,1) smoothing term, then is blended with the operator forecast by:

`weight = resolved_samples / (resolved_samples + probability_prior_strength)`

The resulting probability therefore cannot jump directly to a tiny-sample raw win rate.

Effort calibration uses the segment's ratio of total actual effort to total estimated effort, shrunk toward factor `1` by `effort_prior_strength`. The final factor is bounded to `[0.25, 4]` so one pathological history cannot collapse or explode a current estimate without an explicit reason code.

Below `minimum_samples`, estimates are preserved exactly and the receipt says why.

Confidence is evidence-count metadata only:

- `INSUFFICIENT`: below the minimum;
- `LOW`: minimum through 9 samples;
- `MODERATE`: 10–29;
- `HIGH`: 30+.

It is **not** a probability that the opportunity will pay.

## Receipt and replay safety

Every compiled result contains `opportunity-forecast-calibration-receipt/v1` with:

- normalized history digest;
- normalized current-candidate-set digest;
- calibration result digest;
- trusted cutoff and exact parameters;
- receipt digest.

`verify_forecast_calibration(...)` recompiles from supplied history/candidates and requires byte-equivalent canonical semantics. Changed history, candidate source identity, parameters, output, or receipt data fails verification.

`apply_calibration_to_ranker_candidate(...)` is a non-mutating adapter. A ranker candidate must explicitly carry calibration candidate ID, canonical source URL, segment, source digest, and the original estimates. Any transplant or changed-original-estimate attempt fails. The helper changes only the two ranker estimate fields and adds a calibration metadata envelope; it does not touch snapshots, reward data, or eligibility evidence.

## CLI

Compile a new create-exclusive receipt file:

```bash
python -m concierge.forecast_calibration compile \
  --history history.json \
  --candidates candidates.json \
  --trusted-cutoff 2026-09-13T12:00:00.000Z \
  --output calibration.json
```

Verify it offline against the same evidence inputs:

```bash
python -m concierge.forecast_calibration verify \
  --history history.json \
  --candidates candidates.json \
  --receipt calibration.json
```

The compiler refuses to overwrite an existing output path. This keeps a previous receipt from being silently replaced by a changed run.

## Authority ceiling

Calibration establishes only `forecast = calibrated_estimate_only`. It explicitly grants **no** authority for:

- eligibility or availability;
- reward amount or currency conversion;
- ownership/collision resolution;
- outreach or submission;
- contract/bounty award;
- customer acceptance;
- settlement or payment;
- realized revenue.

Use the existing intake, ranking, allocation, submission-custody, settlement, and closeout rails for those independent boundaries.
