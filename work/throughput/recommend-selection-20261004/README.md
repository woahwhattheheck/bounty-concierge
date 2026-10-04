# Recommendation selection allocation

`recommend` used to copy every input dictionary before sorting, even when the caller requested only ten results. Partial-result calls now retain the highest-scoring candidates and copy only the returned rows. Full-result calls keep the existing dictionary sort. Scores, stable ordering for ties, shallow result copies, and input dictionaries are preserved.

## Recorded result

The unchanged complete `recommend` call was measured on two retained inputs, using their shared nine-skill catalog and `limit=10`. Seven alternating baseline/candidate pairs were recorded separately for untraced timing and traced allocation, after three warmup pairs.

| Retained input | Peak traced allocation, before → after | CPU median, before → after |
|---|---:|---:|
| 266-row bounty index | 277,776 → 154,144 bytes (44.5% lower) | 53.320 → 54.237 ms |
| 26-row BountyHub safe catalog | 28,101 → 14,705 bytes (47.7% lower) | 0.256 → 0.299 ms |

The measured improvement is allocation. CPU medians increased by 0.917 ms and 0.043 ms respectively; these measurements do not establish a latency improvement. Peak traced Python allocation is distinct from process RSS and fleet memory. This was a shared cloud host. No provider requests were made by the benchmark.

All 48 full-output comparisons matched across four skill selections and six limits per input. Four independent tie/copy controls checked ordering, score replacement, shallow-copy behavior and unchanged input dictionaries. These output checks include empty skills, no matches, nonpositive limits, one result and all results. Allocation samples cover `limit=10`; other limits have output checks only.

## Reproduce

The baseline is `6d05a0b0f8d2af49152bb0fb1cd716cece6d2b16`, source blob `c779e912c5b7a3a23a764762f9f4ececc0235f85`. Exact source/input SHA-256 hashes, environment and every raw sample are in `results.json`.

From the repository root, using the retained input files whose hashes match the result:

```bash
git show 6d05a0b0f8d2af49152bb0fb1cd716cece6d2b16:concierge/skill_matcher.py > /tmp/recommend-baseline.py
python work/throughput/recommend-selection-20261004/benchmark.py \
  --baseline /tmp/recommend-baseline.py \
  --candidate concierge/skill_matcher.py \
  --data-root . \
  --index data/bounty_index.json \
  --catalog work/supply/bountyhub/2026-10-04-catalog-56f3.json \
  --output /tmp/recommend-selection.json
```

Input parsing, imports, output serialization and explicit pre-call garbage collection are outside the measured call. Both source versions load the same skill-tag file. Retained listing data is used for reproducibility and does not represent newly checked bounty availability or payment state.
