# Governance proposal — synthetic labels and scores

This proposal answers Gryd-lock/grydlock-testkit #73 against pinned current source. It deliberately separates **ground-truth label decisions**, **numeric score calibration**, and **product warning policy**. Those are related but not interchangeable.

## Decision summary

Use a **hybrid governed-evidence model**:

- Labels are accepted only from synthetic evidence plus independent human review.
- Scores are assigned only after the label decision, under a versioned calibration manifest with explicit anchors.
- Product warning-tier mapping remains a separate downstream policy contract and is recorded as a compatibility check, not silently treated as the definition of the label.
- Every changed label or score gets a machine-readable provenance receipt bound to the exact fixture/data revision.
- CI verifies the receipt structurally and cryptographically by content hash; it does not attempt to automate expert judgment.

This keeps the useful nuance of review without making the dataset depend on undocumented reviewer intuition.

## Model comparison

### Model A — independent expert review

Process:
1. Author supplies synthetic construction evidence, risk-pattern evidence, counterevidence, and privacy attestation.
2. Two reviewers independently choose a label **before seeing a proposed score**.
3. Agreement accepts the label; disagreement requires an adjudicator.
4. A separate score reviewer/calibration step chooses a value from the current calibration manifest.

Strengths:
- Handles new attack shapes and ambiguous synthetic scenarios.
- Makes disagreement visible instead of hiding it in a single notes sentence.
- Can distinguish evidence from policy exceptions.

Weaknesses:
- Higher reviewer cost.
- Agreement can look reproducible while reviewers share the same bias.
- Free-text rationales alone remain hard for CI to validate.

### Model B — deterministic rules / scorecard

Process:
1. A versioned rule set maps constructed synthetic features and risk patterns to label.
2. A deterministic calibration function maps severity features to score.
3. CI recomputes both outputs from declared evidence.

Strengths:
- High repeatability and low reviewer effort.
- Easy to diff and reproduce.
- Makes score drift immediately detectable.

Weaknesses:
- Encodes current assumptions as if they were ground truth.
- Easy to game if evaluation fixtures are generated from the same rules they measure.
- Novel fraud patterns force rule changes and can retroactively change the dataset.

### Recommended hybrid

Use expert review for the semantic label and a deterministic/versioned calibration policy for numeric placement. Require explicit adversarial-clean controls and agreement records. This prevents the evaluation set from becoming a tautology while still keeping scores reproducible.

## Required evidence — label changes

A label addition/change must carry a review receipt containing:

- fixture id and fixture content hash
- prior label, proposed label, and change type
- synthetic source recipe or scenario references
- declared `risk_pattern`
- positive evidence: which constructed behaviors support the label
- counterevidence / alternative explanation
- at least one adversarial-clean control for a newly introduced risk pattern
- two independent reviewer decisions with stable reviewer identifiers
- adjudicator decision when reviewers disagree
- privacy attestation: synthetic-only; no live user/customer/network identity imported
- dataset-balance delta by label and risk pattern
- linked issue/PR and timestamp
- schema version

A desired numeric score is **not** valid evidence for a label. Reviewers making the label decision should not be shown the proposed score.

### Agreement rule

Per-change gate: 2 independent reviewers must agree, otherwise a third adjudicates.

Batch health metric: report raw agreement and Cohen's kappa (or another predeclared categorical agreement statistic) across a meaningful review batch. Do not turn the batch statistic into a truth oracle; it is a signal that the rubric may be ambiguous.

## Required evidence — score changes

A score addition/change must carry, separately:

- accepted label receipt id/hash
- prior score and proposed score
- calibration manifest version/hash
- anchor fixture ids used for comparison
- synthetic severity features used to place the item among those anchors
- boundary sensitivity: whether ±1 or a plausible evidence change crosses a product warning tier
- downstream warning tier before/after under the pinned research/product policy
- reason for the change and whether it is calibration-wide or fixture-specific
- exception id if outside ordinary calibration rules
- reviewer identity for the numeric decision
- exact `scores.json` content hash after the proposed change

A changelog sentence is useful release context but is insufficient authorization for a rescore.

## Calibration method

Do not calibrate by maximizing agreement against the same labels that are later used to report "accuracy"; that is circular.

Instead:

1. Version a `calibration-manifest` containing the label taxonomy, product warning-tier policy reference, per-label anchor fixtures, and the severity features that order fixtures **within** a label.
2. Select anchors from synthetic cases whose construction makes relative severity explicit.
3. Include adversarial-clean controls: scenarios containing superficially suspicious signals that are intentionally benign.
4. Place new scores by interpolation/ranking relative to anchors, not by aesthetic "spread".
5. Run the full fixed dataset through the downstream policy and report:
   - label distribution
   - risk-pattern distribution
   - warning-tier distribution
   - cross-tab of label × warning tier
   - any score within a predeclared boundary margin
6. Any calibration-manifest change is a dataset-policy change: version bump, full recomputation, review, and a new locked manifest digest.

The existing actual scores (clean 2–6, suspicious 55–62, malicious 85–97) can be grandfathered as **legacy observations**, but their historic reviewer/calibration evidence must not be fabricated. Backfill them with a receipt whose provenance is explicitly `legacy_import` and references the pinned commit and existing notes.

## Product-tier compatibility

Current research policy is 0–20 Low / 21–50 Elevated / 51–75 High / 76–100 Critical, while current testkit prose permits clean 0–25 / suspicious 40–70 / malicious 75–100.

Do not silently "fix" one side in #73. First decide and document the intended semantic relationship:

- If labels are expected to correspond to warning severity, align the calibration bands with the product tiers and migration-review every affected score.
- If labels and warning tiers intentionally differ, add a declared compatibility matrix and require every permitted cross-label/tier case to have a rationale and evaluation semantics.

Either choice is defensible; the undocumented overlap is not.

## Provenance record sketch

Suggested additive path after assignment:

`governance/reviews/<fixture-id>/<revision>.json`

Example fields:

```json
{
  "schema_version": "1.0",
  "fixture_id": "<stable synthetic fixture id>",
  "change_type": "score",
  "fixture_sha256": "<hash of normalized fixture>",
  "label": {
    "prior": "suspicious",
    "proposed": "suspicious",
    "evidence_refs": ["scenario:..."],
    "reviewers": [
      {"id": "reviewer-a", "decision": "suspicious"},
      {"id": "reviewer-b", "decision": "suspicious"}
    ],
    "adjudication": null
  },
  "score": {
    "prior": 58,
    "proposed": 60,
    "calibration_version": "v1",
    "anchor_refs": ["fixture:anchor-low", "fixture:anchor-high"],
    "boundary_sensitivity": "does-not-cross-current-warning-tier"
  },
  "privacy": {"synthetic_only": true, "live_identity_data": false},
  "exception": null
}
```

The identifiers above are illustrative schema values, not claims that those reviewers or anchors currently exist.

### Privacy alignment

Follow the adapter's provenance principle: collect only what is needed to reproduce the decision. Fixture governance should not ingest raw live addresses, provider errors, RPC traces, or user data. Reviewer identity may be a stable project identifier; it does not need unrelated personal information.

## Example reviewed change

Use the existing suspicious trustline fixture `GCHYSQ57SVW6LFLGLQ4P77ZDQJ7BPQIM3QOCPIBIZKXGZGAQMJQZRFMS` only as a **worked, non-executed example**:

Current source:
- label: suspicious
- risk pattern: scam-trustline
- score: 58
- notes: trustline to the synthetic SCAM asset, no other activity

Example process:
1. Label reviewers receive the synthetic fixture/scenario evidence and an adversarial clean control representing an ordinary trustline. They independently decide whether `suspicious` remains supported. The score 58 is hidden during this step.
2. If they agree, label receipt result is `NO_CHANGE`; disagreement is adjudicated.
3. A score reviewer then compares the fixture against versioned suspicious anchors. If a future calibration manifest justified 60 rather than 58, that would be a **score-only** proposal with its own evidence.
4. The cross-repo compatibility check confirms whether 58→60 changes the warning tier (under the pinned research policy, both are High).
5. CI binds the receipt to the exact destination/score hashes and rejects unexplained mutation.

No 58→60 rescore is recommended or performed by this audit; the numbers only demonstrate how a reviewed score-only change would be represented.

## Exceptions

An exception must be explicit and bounded:
- stable `exception_id`
- owner/reviewer
- reason
- exact fixtures/fields affected
- created time
- expiry or review-by date
- linked issue/decision
- whether downstream evaluation excludes or specially classifies the item

A changelog line, PR label, or maintainer comment alone should not become a permanent machine-readable exception.

## Dataset balance and adversarial controls

The existing validator checks minimum counts by label, which prevents accidental deletion but does not establish balanced coverage.

Track at least:
- count by label
- count by `risk_pattern`
- entity type
- scenario participation
- warning tier under current product policy
- positive vs adversarial-clean controls

For each new risk pattern promoted to meaningful evidence, add or identify a clean control that contains a nearby benign signal. Without negative controls, the dataset can reward detectors for simplistic heuristics and report misleading accuracy.

## Assignment-ready implementation slices

After provider/maintainer assignment, one coherent upstream PR can deliver:

1. `docs/governance/label-score-governance.md` — policy, label/score separation, review flow, exceptions.
2. `governance/calibration.json` — versioned bands/anchors/policy references.
3. `governance/reviews/` — receipts; legacy fixtures explicitly imported without fabricated review history.
4. `scripts/validate-governance.mjs` — diff-aware checks that every changed label/score has a matching receipt, hashes match, reviewer requirements are met structurally, and exception/calibration references resolve.
5. CI wiring so fixture mutation cannot merge with only a free-text changelog.
6. Tests for:
   - label change without receipt → fail
   - score change without accepted label provenance → fail
   - fabricated/mismatched content hash → fail
   - same reviewer duplicated twice → fail
   - disagreement without adjudicator → fail
   - expired/unknown exception → fail
   - calibration version missing/unknown → fail
   - boundary-crossing score records downstream tier before/after
   - legacy import is explicit and immutable
   - adversarial-clean requirement for a new risk pattern
   - valid reviewed no-op/change → pass

## Research handoff

The research repository should consume the locked governance/calibration version when reporting accuracy and false-positive rate. Reports should name:
- testkit commit/release
- governance schema + calibration version
- product warning-tier policy version
- counts/cross-tab by label and risk pattern

That makes evaluation repeatable without claiming that a synthetic label is objective real-world truth.
