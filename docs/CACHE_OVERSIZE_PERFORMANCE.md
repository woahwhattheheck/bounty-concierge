# Text-heavy cache page allocation

Measured 2026-10-04 with Python 3.13.5 on x86-64 cloud Linux, using the actual standalone `PageCache.store` and `PageCache.load` module entry points. This is a local synthetic workload, not a live provider, quota or earnings measurement.

Baseline source blob: `45b30336f0b3e9cbd15a1fa4d87d41ecf9d9a5d5`.
Delivered source blob: `0e6cab1240839c438dc664c51296ab89890afa68` ([source commit](https://github.com/woahwhattheheck/bounty-concierge/commit/3e0b45246b5ccb0a771f5299de9fa2bdc4df2d63)).

## Behavior and tradeoff

The previous writer constructs the entire canonical entry and checksum envelope before rejecting a file above 8 MiB. The updated writer keeps the single-pass encoder for ordinary pages. Text-heavy lists use eight-issue batches and discard retained encoded pieces once the complete-envelope allowance is exceeded. Later batches still pass the canonical encoder, preserving `error` for malformed JSON even after the page is already too large.

A shallow top-level text estimate selects the optimization; only actual encoded size controls admission. This is **not a strict memory ceiling**: a single huge issue, deeply nested content, or a small issue count can still require a large allocation. The loader, ETag revalidation, authorization partition, atomic replacement and temporary-file cleanup are unchanged. Existing cache files require no migration.

| Measured operation | Baseline | Delivered |
|---|---:|---:|
| Ordinary 100-issue page, median complete store | 1.0666881 ms | 1.1016502 ms |
| Ordinary page, traced allocation peak | 597,106 B | 597,106 B |
| Oversized Unicode-heavy 100-issue page, median store | 35.373688 ms | 14.905245 ms |
| Oversized page, traced allocation peak | 38,416,738 B | 10,758,288 B |

The oversized sample used 72.00% less peak traced allocation and 57.86% less time. Ordinary writes were 0.0349621 ms (3.28%) slower, so this is not an across-the-board speedup. An initial all-pages batched candidate was discarded because it slowed ordinary writes more.

There were seven alternating-order paired timing samples: ten complete ordinary writes per sample, one oversized write per sample. Normal writes include encoding, checksum, file write, flush, fsync and replacement. Traced peaks are separate single executions, with inputs allocated before tracing; they are not process RSS or input-memory totals. Local storage timing is not a persistent-disk durability benchmark. No full-package suite or provider request was run.

Raw timing samples, milliseconds per store:

```json
{
  "ordinary_before": [1.0500814, 1.0467024, 1.0922912, 1.06997, 1.0751107, 1.0666881, 1.0482046],
  "ordinary_after": [1.0886408, 1.1563587, 1.1094468, 1.0557459, 1.0897064, 1.1016502, 1.1077904],
  "oversized_before": [35.373688, 36.923046, 33.505596, 37.134271, 33.422452, 36.278506, 33.857429],
  "oversized_after": [15.353774, 12.106997, 14.905245, 12.965506, 14.913247, 12.108228, 14.917364]
}
```

Three focused behavior groups were executed. Empty, nested Unicode/control content and a batched 100-issue accepted page produced identical files and loaded values (214, 318 and 802,603 bytes). An exactly 8,388,608-byte envelope stored; adding one byte skipped while preserving the old file. A nonfinite value in the final oversized batch, unsupported JSON and an injected replacement failure all retained `error`, the original file and zero leftover temporary files. These are scoped behavior checks, not a recreated repository test suite.

## Reproduce the measurements

From a checkout containing both source blobs, prepare a disposable directory:

```sh
work=$(mktemp -d)
git cat-file blob 45b30336f0b3e9cbd15a1fa4d87d41ecf9d9a5d5 > "$work/baseline.py"
git cat-file blob 0e6cab1240839c438dc664c51296ab89890afa68 > "$work/candidate.py"
```

Save the following as `$work/measure.py`, then run `python "$work/measure.py"`. It makes no network requests and writes its JSON result beside the script. Both workloads below are synthetic; the example body text is not a retrieved provider record.

```python
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).parent

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

old, new = load('baseline'), load('candidate')
body = ('A retained cache example: ordinary text, Unicode μ and control\\n. ' * 40)
small = [{'number': i, 'title': 'Example issue', 'body': body, 'labels': ['bounty'], 'user': {'login': 'example'}} for i in range(100)]
# A 16k-codepoint body is 64k UTF-8 bytes but expands under canonical ensure_ascii.
large = [{'number': i, 'title': 'Unicode-heavy issue', 'body': '😀' * 16000, 'labels': ['bounty']} for i in range(100)]
output = {}
with tempfile.TemporaryDirectory() as work:
    for label, rows, batches in [('accepted',small,10),('oversized',large,1)]:
        caches = {name: module.PageCache(Path(work)/label/name, 'synthetic-token') for name,module in [('baseline',old),('candidate',new)]}
        statuses = {name: cache.store('example/project',1,'"v1"',rows,False) for name,cache in caches.items()}
        assert len(set(statuses.values())) == 1, statuses
        if label == 'accepted':
            snapshots=[next(cache.root.glob('*.json')).read_bytes() for cache in caches.values()]
            assert snapshots[0] == snapshots[1]
            assert caches['baseline'].load('example/project',1) == caches['candidate'].load('example/project',1)
        else:
            assert all(not cache.root.exists() for cache in caches.values())
        timings={name:[] for name in caches}
        for trial in range(7):
            for name in (list(caches) if trial%2 == 0 else list(reversed(caches))):
                t=time.perf_counter_ns()
                for _ in range(batches):
                    assert caches[name].store('example/project',1,'"v1"',rows,False) == statuses[name]
                timings[name].append((time.perf_counter_ns()-t)/batches/1e6)
        peaks={}
        for name in caches:
            gc.collect()
            tracemalloc.start()
            caches[name].store('example/project',1,'"v1"',rows,False)
            _,peaks[name]=tracemalloc.get_traced_memory()
            tracemalloc.stop()
        output[label]={'status':statuses['baseline'],'samples_ms':timings,'medians_ms':{k:statistics.median(v) for k,v in timings.items()},'peak_bytes':peaks}
print(json.dumps(output,indent=2))
(ROOT/'measurements.json').write_text(json.dumps(output,indent=2)+'\n')
```
