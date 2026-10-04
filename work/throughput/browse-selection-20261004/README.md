# Limited bounty browse selection

`browse --limit N` now selects the top N display rows without sorting every
undisplayed match. This applies to live and `--index` browse. The source is still
fully read and filtered; all match counts, prior-selection counts, completion
metadata and partial-result exit codes remain unchanged. No provider request is
removed or added. Default direct `_filter_bounties` calls retain the full sorted
list. Equal reward keys preserve input order; unknown evidence stays distinct
from explicitly matched zero. No result cache is introduced.

The implementation uses `heapq.nlargest`, which falls back to full sorting when
the requested display covers the input. Filtering still retains its matching
list; this is bounded *selection*, not bounded total input memory.

## Executed result

Python 3.13.5; five alternating timed pairs after equivalence execution. Actual
production filtering and selection, including key construction; source reads,
JSON parsing, imports and output rendering are outside the timed interval.

| Input | Display limit | Before median ms | After median ms | Before traced peak bytes | After traced peak bytes |
|---|---:|---:|---:|---:|---:|
| Retained 266-row index | 10 | 0.262 | 0.220 | 8,512 | 2,888 |
| 100,000 local rows | 10 | 133.537 | 84.438 | 10,282,408 | 800,880 |
| 100,000 local rows | 1,000 | 138.277 | 90.790 | 10,282,384 | 871,488 |
| 100,000 local rows, 50,030 matches | 10 | 155.118 | 131.351 | 5,131,864 | 1,244,664 |

Trace peaks exclude input allocation and were observed separately from timing.
The 100,000-row top-10 result uses 36.77% less measured time and 92.21% less
traced selection/filter allocation. The tiny retained-index timings are noisy;
no overall CLI, network, live-fleet speedup or quota increase is claimed.

Five focused checks cover stable ties/unknown/zero/display boundaries; filters,
source immutability and full-helper compatibility; the actual offline report;
partial live-command outputs and exit codes with a supplied collector report;
and changed-input freshness. Displayed row identities and full counts match.
The live-command check does not contact a provider. No broad suite or hosted
validation run was added. One later source-only composition job published the
exact executed postimage; its controller workflow is outside the product tree.

## Source and reproduction

Prior CLI blob: `9a5ac2c9ae3c4a06907b357fb4d0f99e31183154`, from commit
`dfb5df460f4f75d73ad56b4ba91a1e9c5487c12d`. Candidate CLI blob:
`5474757128ddcb01d849f2503a9a678f159ce66f`. The source bundle is
`4f5bc01a9b6d6b91d5937d9743ed3608f9003ebc`, with the later CLI command additions
composed and checked against the prior blob before execution. The consumed
reward module is `81c16896be453ffd90233ac1c10127ea21e682be`; the retained index
is `36a00d95a1133db1087ed351d88bda37c4f7ea0a`. Source-only publication run
`37202802669` produced commit `23904729dadceaf1e658d7696f866031f2c01d1a`.

```sh
git show dfb5df460f4f75d73ad56b4ba91a1e9c5487c12d:concierge/cli.py > /tmp/browse-before.py
PYTHONPATH=. python work/throughput/browse-selection-20261004/measure.py \
  --before-file /tmp/browse-before.py
```

Raw samples and source identities are in `results.json`. The standalone
measurement is not a CI gate; it runs locally without provider calls. A changed
retained index will produce a different small-index measurement.
