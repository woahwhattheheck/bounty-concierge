# Stop recommendation scoring once the result is determined

`concierge.skill_matcher.recommend` retains the existing bounded-heap selection
and stable tie order. When it has seen `limit` scores of exactly `1.0`, it can
stop: no later score can be higher, and later equal scores lose the stable tie.
With no requested skills, every score is zero, so the answer is a copied input
prefix. The all-results path still returns every row in stable score order.

There is no persistent cache, new provider request, eligibility rule, or required
workflow step. Skill-tag changes remain visible on each new call. This only
optimizes selection from the rows the caller already supplied; it does not make
those rows live, funded, assigned, or payable.

## Reproduce

```sh
python scripts/benchmark_skill_match_ceiling.py --repeat 11 --number 10
python scripts/benchmark_skill_match_ceiling.py --rows 266 --repeat 11 --number 10
```

The comparator body is the pre-change `recommend` implementation from matcher
blob `ad17253542b6d8e81e9dc245036e7d284c85183d`, including the prior bounded-heap
optimization. Both paths call the same production scorer using identical global
lookups. The script loads the matcher source directly, without bootstrapping
unrelated package policy code. Counter instrumentation is outside timing, and timed before/after order
alternates. The script checks 2,800 deterministic input/skill/limit combinations,
input immutability, fresh result dictionaries, dynamic tags, and exact scoring
counts. These checks run offline without a new test dependency.

## Measurements

Python 3.13.5, isolated cloud matcher execution, 2026-10-04. Source matcher and
`data/skill_tags.json` were reconstructed from connector reads and verified against
their Git blob hashes before modification. Catalog tag blob:
`9273087984b9a2a4c531a57190c3ee0da6455440`.

Controlled synthetic rows; **not a live catalog, production traffic, whole-package
bootstrap, or end-to-end fleet benchmark**. Medians of 11 alternating batches of
10 calls, with `limit=10`:

| Rows | Scenario | Rows scored before/after | Before ms | After ms |
| ---: | --- | ---: | ---: | ---: |
| 10,000 | Every row is a full match | 10,000 / 10 | 22.892035 | 0.037242 |
| 10,000 | Every tenth row is a full match | 10,000 / 91 | 21.607169 | 0.210900 |
| 10,000 | No full match | 10,000 / 10,000 | 20.611546 | 20.286047 |
| 10,000 | Empty skills | 0 / 0 | 0.736287 | 0.001347 |
| 266 | Every row is a full match | 266 / 10 | 0.629547 | 0.037954 |
| 266 | Every tenth row is a full match | 266 / 91 | 0.601482 | 0.217949 |
| 266 | No full match | 266 / 266 | 0.597322 | 0.589145 |
| 266 | Empty skills | 0 / 0 | 0.042873 | 0.001187 |

The no-full-match result is effectively unchanged in these measurements; the
small difference is not a claimed optimization. An earlier sequential timing
showed a slowdown that did not reproduce under alternating paired measurements.
Absolute timing is environment-dependent. The durable benefit is avoided scoring
once the top result is mathematically fixed. Empty skills already skipped text
scoring before the change, but still walked the whole input through the heap.
