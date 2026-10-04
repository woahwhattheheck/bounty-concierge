# Source-bundle selector matching measurement

The unpacker compared every archive member against every requested path. This change uses member/ancestor dictionary lookups when there are fewer ancestors than selectors, and retains direct comparisons for shorter selector lists. It marks every matching ancestor so overlapping directory and file selections still count. Archive spooling, complete digest/size validation, tar scanning, extraction, and failure cleanup are unchanged.

## Observed result

Recorded 2026-10-04 with Python 3.12.14 on shared Linux x86-64. Fixtures and extraction used `/dev/shm` (tmpfs); the shared workspace overlay was full. Five runs per implementation and workload alternated execution order.

| Synthetic v1 workload | Selected output | Median before | Median after |
| --- | --- | ---: | ---: |
| 4,096 available files; 258 requested / 257 unique paths | 316 files / 20,224 bytes | 532.022 ms | 351.253 ms |
| 32 available files; one requested path | One file / 64 bytes | 1.754 ms | 1.658 ms |

The multiple-path case had a 33.98% lower observed median. The small case showed no observed regression; its timing difference is too small to support a precise speedup claim. The samples reflect shared-load variation and synthetic fixtures, not provider or fleet throughput.

The timer surrounds `unpack_bundle`, including ZIP reads, complete archive SHA-256 verification, tar scanning, and selected filesystem extraction. Module loading, output comparison, and cleanup are outside the timer. The fixtures use 64-byte files, grouped into 64-file directories. Manifest repository/commit/tree identities are explicit fixture markers, not real GitHub source provenance.

## Focused equivalence

All returned fields except the varying destination root, selected file bytes, and regular-file modes matched across all runs. The multiple-path fixture includes duplicate selectors and an overlapping directory/file selection. Component-boundary and missing-selector cases produced the same errors and removed the partial destination in both implementations. These two error cases use the retained direct-comparison branch; the multiple-path fixture exercises ancestor matching.

No provider calls, source export, installs, general test suite, or CI execution was used for this measurement. Temporary fixture and extraction directories were removed.

## Source identity and reproduction

| Source | Git blob |
| --- | --- |
| Released baseline | `1ba52712f06fe5303214231fe5e4a589889d56ac` |
| Executed baseline | `f09d519b4419701b1e70589ea69312f651e94371` |
| Executed and published candidate | `62bcc86ac7819e8afe83830c7fb2a5835863b518` |

The executed baseline differs from the released baseline only by one additional terminal LF introduced when saving the measurement copy. The candidate is published byte-for-byte as executed. Full source SHA-256 digests, every timing sample, fixture ZIP digests, output counts, and rejection results are in [receipt.json](receipt.json). [measure.py](measure.py) constructs and removes the same deterministic fixtures; it deliberately uses tmpfs for both fixture storage and the unpacker's temporary archive spool.

To reproduce from a local Git checkout containing the source objects, create the exact source copies:

```python
from pathlib import Path
import subprocess

root = Path("/dev/shm/unpack-selector-repro")
root.mkdir(exist_ok=False)
before = subprocess.check_output(
    ["git", "show", "1ba52712f06fe5303214231fe5e4a589889d56ac"]
)
after = subprocess.check_output(
    ["git", "show", "62bcc86ac7819e8afe83830c7fb2a5835863b518"]
)
(root / "before.py").write_bytes(before + b"\n")
(root / "after.py").write_bytes(after)
```

Then run the retained measurement from the repository root:

```sh
python3 work/throughput/source-unpack-prefix-20261004-b3ad/measure.py --before /dev/shm/unpack-selector-repro/before.py --after /dev/shm/unpack-selector-repro/after.py --output /dev/shm/unpack-selector-repro/receipt.json
```

Timing values will vary. This records the selector change in isolation; any later memory or transport improvement needs its own source-bound result.
