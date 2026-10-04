# Acceptance action-distance lookup

The receipt compiler already finds non-negated action offsets in increasing order.
Its old distance predicate scanned every offset for every candidate target match.
The new predicate uses binary search over that same list, checking the two
inclusive 220-character endpoint windows separately. Long-span interior points
remain excluded. No patterns, negation, normalization, sentence boundaries,
limits, policy, freshness, URL handling or receipt fields change.

## Actual local measurement

Measured the complete production `_compile_bounty_acceptance_safety_gate_at`
function, loading original and modified source modules independently. Alternating
before/after order; one excluded warm-up; 5 paired ordinary samples and 3 paired
samples per repetitive scenario. A fixed evaluation clock permits exact equality
of the entire returned receipt, including its digest.

| Input recipe | Total characters | Before median (ms) | After median (ms) |
| --- | ---: | ---: | ---: |
| ordinary_requirements_120 | 11,920 | 8.968219 | 8.978835 |
| distant_terms_1000 | 18,400 | 120.511717 | 8.923051 |
| distant_terms_2000 | 36,400 | 368.437808 | 17.467469 |
| distant_then_near_match_2000 | 36,417 | 368.443389 | 14.698682 |

The ordinary batch has 120 requests (12 controls repeated ten times) and shows
no material improvement. The 18,400/36,400-character repetitive sentences are
synthetic stress inputs below the existing 250,000-character limit, not typical
bounty text. The positive final-match case also preserves its hold disposition.
There is no live-provider, end-to-end supply, fleet-throughput or network claim.

All complete receipts were identical in every measured sample. The same local
execution checked 265 distance cases (including inclusive/exclusive boundaries,
empty/duplicate offsets and the separated windows of long spans) and 12 text
controls (negation, Unicode, sentence separation and `.env` included). No full
suite, hosted runner, dependency installation or provider request was performed.

## Source and reuse

- Original Git blob: `6d2545111f9e80e203a7eb09d5af2bab8fa24f29`.
- Measured replacement Git blob: `785a510af216e27cf96e2f2903796ed127a54732`.
- Original module is present at repository commit
  `00884453d3b2743971bd74d9d71782767d37588f` and the retained
  `4f5bc01a9b6d6b91d5937d9743ed3608f9003ebc` source bundle.
- Full source SHA256 identities, Python/platform, every timing sample and compact
  content/receipt digest-array hashes are in `results.json`.
- Measured Python: `3.13.5 (main, Jul 15 2026, 20:25:40) [GCC 14.2.0]`.

From a checkout retaining the original commit:

```sh
git show 00884453d3b2743971bd74d9d71782767d37588f:concierge/bounty_acceptance_safety_gate.py > /tmp/acceptance-before.py
python work/throughput/acceptance-action-index-20261004/measure.py \
  --before /tmp/acceptance-before.py \
  --output /tmp/acceptance-action-index-results.json
```

Normal bounty routing imports the optimized helper automatically. No new command,
cache, queue or approval step is required. Independent URL-validator work can be
composed without changing this distance-lookup scope; these measurements apply
to the source blobs stated above, not an unmeasured combined successor.
