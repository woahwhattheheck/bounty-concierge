# Bounty page-cache load memory

Measured 2026-10-04 with CPython 3.13.5 on Linux x86_64. The change is in the existing `concierge/bounty_cache.py`; no new cache, network worker, test suite or provider call is introduced.

## Source and behavior

Baseline: commit `b656023350fbb9bb5fb90b5b7e4a165a4047ebcd`, source blob `0652236707e669b56fe533f5589319453c61cf3a`.

Measured candidate source blob: `fc8422c74fc879fcc971ab5353ac4304f46454a7`.

A loaded large page previously held both its raw bytes and decoded JSON text during parsing, then allocated a whole canonical JSON buffer for checksum verification. Either allocation could determine peak memory. The repair releases the byte input before parsing using the same detected encoding and `surrogatepass` behavior as `json.loads(bytes)`, and shares the existing text-heavy issue batches between storage and incremental checksum verification. Ordinary serialized pages keep the direct checksum encoder.

The 8 MiB file limit, 100-issue limit, canonical on-disk bytes, checksum values, ETag behavior, atomic writes, authorization partition and provider-revalidation requirement remain unchanged. No cache entry becomes offline authority. Oversized serialization still visits the remaining values, preserving errors on malformed later values rather than treating them as a successful skip.

## Complete-load measurements

| Synthetic page | File bytes | Peak traced allocation, bytes | Median load time, ms |
| --- | ---: | ---: | ---: |
| small | 9,794 | 38,600 → 37,490 (2.88% lower) | 0.102 → 0.103 |
| large_ascii | 6,004,194 | 18,040,984 → 12,036,757 (33.28% lower) | 37.172 → 29.724 |
| large_mixed | 6,504,194 | 21,092,354 → 14,588,127 (30.84% lower) | 35.210 → 32.968 |

Each large timing is one complete `PageCache.load`, with five alternating before/after observations. The ordinary page uses five alternating batches of 25 complete loads; figures are per-load batch means, then their median. Timing runs are untraced and disk-cache-warm. Traced peak allocation was measured separately; it is not RSS or a fleet throughput claim. The small-page median increased by about 0.001 ms in this run. Large-page latency results are local observations, not promised speedups.

`measurement.json` retains every sample, exact source identities, interpreter/platform and compatibility outcome. The comparison also executes both real `store` and `load`: accepted disk bytes and returned values match for ordinary, empty-ETag and Unicode pages; oversized pages remain skipped; malformed data after oversized batches remains an error. UTF-8/BOM, UTF-16 and UTF-32 inputs, checksum damage, invalid JSON/encoding, oversized files and misses retain their prior load outcomes. No HTTP/provider/CLI/full-package integration or concurrent-filesystem guarantee is established by these measurements. The batching remains a shallow optimization, not a bound on individual or deeply nested values.

## Reproduce

From a repository checkout containing the baseline commit:

```sh
git show b656023350fbb9bb5fb90b5b7e4a165a4047ebcd:concierge/bounty_cache.py > /tmp/page-cache-before.py
python3 work/performance/page-cache-load-20261004/measure.py \
  /tmp/page-cache-before.py concierge/bounty_cache.py \
  --output /tmp/page-cache-measurement.json
```

The command uses temporary local directories and synthetic pages, imports the complete source modules directly, and requires only Python's standard library. It does not install dependencies, dispatch CI or contact a provider. Keep the source blobs alongside any new results when comparing later revisions.
