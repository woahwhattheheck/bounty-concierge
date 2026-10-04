# Large-capture recovery reuse

Measured October 4, 2026 in a cloud Linux container with Python 3.13.5 and Requests 2.32.5. This is an offline, in-process measurement using synthetic closed-issue captures. It is not a live bounty qualification, payment observation, hosted Actions result, or fleet-wide throughput claim.

## Shipped source

- Source commit: `f219e31bca24571bb3469832695ee48b67eac815`.
- Baseline recovery blob: `61f50543d72e994ec27ff7ed24b8507017748c56`.
- Executed and published recovery blob: `d70430083bd994a33d3e861faaca129aca958b8c`.
- Unchanged dependency snapshot for this comparison: `4f5bc01a9b6d6b91d5937d9743ed3608f9003ebc`.
- Reproduction script: [replay.py](replay.py), published at `dd44db33ace2da625dda31f23f449035fabf0ff7`.

The prior cache retained complete raw file bytes as keys, capped at 1 MiB and 128 entries. A larger capture could never be reused; alternating medium files exhausted the byte budget. The replacement computes a SHA-256 digest locally over every byte and retains that 32-byte key. It still has a 128-entry least-recently-used limit: at most 4 KiB of digest payload, plus Python/container overhead. This does not bound all recovery working memory; parsed captures were already retained for output grouping.

Only a successfully replayed capture with matching shortlist identity and submission target enters the cache. The key is not a caller-provided receipt or issue number. Every input is still read and preserved byte-for-byte. Changed input is fingerprinted independently, malformed data and conflicting observations retain their errors, and every recovery invocation starts with an empty cache. The previously merged date-overflow handling is retained. No provider read, clock refresh, payment policy or output schema was added.

## Completed measurements

The first three rows use three alternating baseline/candidate pairs. Remaining rows are focused behavioral comparisons, not speedup claims.

| Workload | Replay calls before / after | Median before / after |
| --- | ---: | ---: |
| Four identical 6,785-byte files | 1 / 1 | 3.824 / 3.445 ms |
| Four identical 1,282,529-byte files | 4 / 1 | 2264.547 / 570.533 ms |
| Four alternating 642,529 / 642,530-byte files | 4 / 2 | 1117.351 / 566.887 ms |
| Changed bytes with unchanged stored receipt, plus malformed JSON | 2 / 2 | 4.024 / 5.035 ms |
| Conflicting observation timestamps | 2 / 2 | 6.029 / 5.844 ms |
| Entry eviction with capacity temporarily set to two | 4 / 4 | 9.361 / 9.511 ms |
| Separate recovery invocation with two large duplicates | 2 / 1 | 1163.367 / 638.399 ms |

All seven comparisons passed. All non-clock summary fields, supply/remaining bytes, retained input bytes and source modification times matched. Output directory/file modes remained 0700/0600. Provider requests: **0**. Large-duplicate recovery was approximately **3.97 times faster** in this workload; alternating medium recovery was approximately **1.97 times faster**. Small-file and one-off timings are not claimed as meaningful improvements.

## Reproduce the pinned comparison

From a full repository checkout containing the named commits, with the reported Python/Requests versions available:

```bash
ROOT=$(pwd)
WORK=$(mktemp -d)
git archive 4f5bc01a9b6d6b91d5937d9743ed3608f9003ebc | tar -x -C "$WORK"
git cat-file blob 61f50543d72e994ec27ff7ed24b8507017748c56 > "$WORK/recovery_baseline.py"
git show f219e31bca24571bb3469832695ee48b67eac815:concierge/bounty_capture_recover.py > "$WORK/concierge/bounty_capture_recover.py"
git show dd44db33ace2da625dda31f23f449035fabf0ff7:work/throughput/recovery-fingerprint-20261004/replay.py > "$WORK/replay.py"
(cd "$WORK" && PYTHONPATH=. python replay.py --baseline recovery_baseline.py --output results.json)
```

The script counts calls to the actual replay function, rather than substituting its decisions, and prevents provider I/O. It writes its own JSON measurements. Timings will vary with host and filesystem. No full suite or end-to-end live-provider run was performed.

## Use

The existing command is unchanged:

```bash
python -m concierge.bounty_capture_recover SOURCE_RUN --output-dir NEW_DIR --json
```

Read the returned summary, then resume only its `remaining.json`. Recovery reuses historical evidence; it does not make that evidence current or authorize a claim.
