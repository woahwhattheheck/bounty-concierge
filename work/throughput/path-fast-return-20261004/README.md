# Canonical source-path reuse — 4 October 2026

The four-line production change reuses already-canonical repository-relative
strings instead of constructing a `PurePosixPath` for every archive member.
Existing rejection checks run first; noncanonical paths retain the original
normalization. Adaptive selector matching, duplicate detection, archive identity
checks, extraction filter, TarInfo cleanup and spool-directory behavior are unchanged.

This integrates only the remaining path hunk from the
[retained donor recovery](https://github.com/woahwhattheheck/commons-ship-enforcer/blob/9358376e4794a82996dd55fa58678c843d3a7b80/work/source-handoffs/dot-recovery-cairn-20261004/HANDOFF.json).
The donor's already-superseded selector hunk and old combined performance figures
were not applied or reused as current measurements.

## Source identity and local measurements

Baseline: `f043a61e9cf81668b136913fa7d507700835d451`, file blob
`1626312ba44794ce0ebabb63b2607505766c6c74`.
Candidate: `92f4c5a98cd088413fd8bf38aa9ff7e545b69d7e`, file blob
`8e40ee08f1e15fe07bd34dcfa5c8a886b7416c8b`.
The published source blob matches the complete module executed locally.

Python 3.13.5 on Linux; one warmup per variant, then seven alternating pairs.
Fixture creation and destination cleanup are outside timing. Times are medians.

| Workload | Before | After |
| --- | ---: | ---: |
| Normalize 10,000 canonical paths | 32.915009 ms | 4.469006 ms |
| Normalize 10,000 noncanonical paths (fallback control) | 34.631623 ms | 34.019379 ms |
| Complete selected unpack of an 8,000-file fixture | 465.934135 ms | 423.965717 ms |

The complete selected unpack took **9.01% less time** in this local comparison.
It scans 8,000 regular files and extracts 102 plus one internal symlink, with four
overlapping/file selectors. Counts, selected paths, file-byte hashes and symlink
targets match. The path microbenchmark is separate; do not multiply its 7.37x
ratio into the complete-unpack result. The fallback timings are approximately level.

Six focused compatibility groups passed: canonical paths; normalization;
invalid inputs; selected extraction/overlap/bytes/symlink parity; missing-selector
cleanup; and preservation of an existing destination. No repository test suite,
network, source-bundle download, provider operation or owner-PC work was run.
No full-extraction, memory, quota, cross-version or fleet-wide speedup is claimed.
The fixture's repeated-digit commit/tree identities are synthetic, not GitHub objects.

## Reproduce without network or dependencies

From a checkout containing these commits, with Python 3.12+:

```sh
base=f043a61e9cf81668b136913fa7d507700835d451
candidate=92f4c5a98cd088413fd8bf38aa9ff7e545b69d7e
work=$(mktemp -d)
git show "$base:tools/unpack_source_bundle.py" > "$work/before.py"
git show "$candidate:tools/unpack_source_bundle.py" > "$work/after.py"
python work/throughput/path-fast-return-20261004/measure.py \
  "$work/before.py" "$work/after.py" "$work/results.json"
cat "$work/results.json"
rm -r "$work"
```

`measure.py` imports the two complete modules and calls their actual production
`_path` and `unpack_bundle` implementations. `results.json` retains every raw
sample, source blob identities, environment and fixture receipt. It is a bounded
manual measurement program, not a new scheduled worker or test gate.
