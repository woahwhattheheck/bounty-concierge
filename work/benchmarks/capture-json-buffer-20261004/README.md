# Capture JSON encoding memory, 2026-10-04

## Result

The existing capture-batch/recovery JSON output helper now keeps its fast JSON
serializer but encodes large output in 64 KiB chunks. The shared exclusive
writer consumes those chunks without joining another complete byte buffer.
Outputs of at most 64 KiB retain a single-buffer write.

This removes an output-sized UTF-8 copy and newline concatenation for large
files; it does **not** make JSON serialization constant-memory. The complete
JSON string and the serializer's own intermediates still exist.

| Synthetic output | Before median | After median | Before traced peak | After traced peak |
| --- | ---: | ---: | ---: | ---: |
| 9,544 bytes | 0.129516 ms | 0.133201 ms | 19,169 B | 21,131 B |
| 23,774,308 bytes | 89.901641 ms | 79.964766 ms | 47,549,529 B | 28,967,513 B |

Large output: **39.08% lower traced peak allocation and 11.05% lower median
elapsed time**. Small output: 3.685 microseconds (2.85%) more median time and
1,962 bytes more traced peak allocation in this run. These are measurements of
this operation and fixture, not a fleet-throughput or end-to-end collection
claim. The small timings are too short to interpret as a stable speed change.

## Reproduction

From a Git checkout containing the baseline commit and this change:

```bash
before=$(mktemp -d)
git archive 2fefacd83611707f7c4a3c229bfdf513f1630e01 \
  concierge/bounty_capture_batch.py concierge/secure_output.py | tar -x -C "$before"
python work/benchmarks/capture-json-buffer-20261004/benchmark.py \
  --baseline "$before" --candidate . --repeats 5 --output /tmp/capture-json-results.json
```

No dependency installation, marketplace request, provider credential, background
job, or complete project test run is involved. The measurement environment was
CPython 3.13.5, GCC 14.2.0, Linux 6.18.44 x86_64, glibc 2.41.

The script extracts the exact production `_write_json` function by AST and uses
the complete actual `secure_output.py` module. It executes real exclusive file
creation, writes and fsync. It does not import or execute `collect_batch`, live
preflight, offline capture replay, or provider transport. The nested fixture is
synthetic JSON shaped like capture output, not a valid receipt or real bounty
observation. Large output contains 1,000 rows with 50 comments each.

Five fresh-process wall-time trials per variant/size run in alternating order.
Memory is a separate single tracemalloc trial: fixture allocation precedes
tracing, and output comparison follows it. Every run checks exact bytes against
the original JSON expression and mode 0600. Tracemalloc peak is not total RAM.
Raw process RSS is retained but is not used for the improvement claim because
it includes interpreter/import/fixture overhead and process high-water effects.

`results.json` retains every raw timing/memory observation as a table, with
repeated source/output identities factored into explicit invariants. The script
emits the same information in expanded per-row form when rerun.

## Source identity

Baseline commit: `2fefacd83611707f7c4a3c229bfdf513f1630e01`. Publication was based
on refreshed main `bf59b8289f6a52903fe8727baf89b96b747be299`; both production blobs
were still the measured baseline. Other contributors' changes were retained.

| File | Baseline Git blob | Measured candidate Git blob |
| --- | --- | --- |
| `concierge/bounty_capture_batch.py` | `91298cf466a23fc796dde9f640d942f975ab737f` | `40f700e150a97cbc631a0fe53c67de9876dea9dc` |
| `concierge/secure_output.py` | `b28696458e11e9498a60e6dd99aaceea468b7f20` | `a0e8953fc8cad9ab62c0b2708325875c4c5e0573` |

Output SHA-256, identical before and after:

- Small: `917db207f1e6fd2a733d7e512f070d8354457b13329af4e68c7792e029a7788a`.
- Large: `427f96cf7ea38104315274f699d04710af28a8663fcd4821ec961db19806ae76`.

## Compatibility and limits

Ten valid JSON cases cover Unicode/surrogates, numeric keys, nesting, and both
sides of the chunk boundary. Five invalid cases retain serialization failure
before output creation. Existing-file refusal remains intact. Seven baseline
bytes-writer checks and fifteen candidate bytes/chunks checks passed: partial
writes, zero-write and fsync failures, existing-file and parent/leaf-symlink
refusal, bytes-type validation, late iterator/chunk errors, and descriptor counts
unchanged at four before/after the grouped checks. Late output failures retain
the created generation rather than deleting a possibly replaced path.

The bytes API, no-follow traversal, exclusive creation, permissions and fsync
remain shared rather than copied. JSON receipt content, ordering, escaping,
newline, and validation flags are unchanged. No principal caching, observation
provenance, request budget, retry policy, or provider decision is modified.
Full application, crash-recovery and live collector performance were not run.

An earlier iterencode/BytesIO prototype was rejected: it reduced large-fixture
traced allocation about 50% but slowed the writer 3.27x. It is not the published
implementation; preserving the fast serializer was the useful tradeoff.
