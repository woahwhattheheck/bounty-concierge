# Carrier-census URL scan — 4 October 2026

The existing census compiler now uses one precompiled Unicode whitespace/DEL
expression instead of invoking a Python generator, `isspace`, and `ord` for
individual URL characters. The two-line production change adds no dependency,
cache, command, network call, approval, or publication path.

Python's Unicode `\s` uses `str.isspace()` semantics; the explicit DEL alternative
preserves the other condition. No ASCII flag is used. Length checks and diagnostic
ordering are unchanged, as are all URL forms, carrier classifications, source
freshness rules, receipt hashes, and authority fields.
Reference: https://docs.python.org/3.13/library/re.html#regular-expression-syntax

## Measured compiler work

CPython 3.13.5, Linux x86_64. The benchmark imports the complete original and
modified production modules. Each workload compiles a synthetic retained snapshot
with the stated number of PRs, including normalization, classification, serialization,
and receipt hashing. Inputs have alternating ordinary/process-closed carriers.
Each version receives ten warmup compilations. Seven sample pairs alternate
execution order; each sample contains 200 complete compilations. Existing URL
parser caches are warm in both versions. No new cache is introduced.

| Carrier rows | Baseline median, ms / 200 calls | Changed median, ms / 200 calls | Reduction |
| --- | ---: | ---: | ---: |
| 1 | 5.659675 | 4.604619 | 18.64% |
| 10 | 22.137162 | 16.434282 | 25.76% |
| 100 | 196.445615 | 146.705380 | 25.32% |

Complete receipts, including their hashes and advisory outcomes, were identical
for all three workloads. Both implementations verified the same receipts.
Focused ASCII/Unicode whitespace and DEL diagnostics retained the same messages.
No maintained tests or validation policy were changed; no full suite was run.

These are local compiler measurements for warm, retained synthetic inputs, not
live GitHub discovery, total application latency, provider quotas, payout
readiness, or a claim that a whole fleet becomes 25% faster. Request counts and
live-visibility cadence are unchanged.

## Reproduce

Baseline parent: `0aa12f48cf37c6128c5fe3eae8fd015feaf3b8a0`.
Baseline module blob: `036a76fa0fe7025daf829304f6bcf34cff59ad62`.
Changed module blob: `1e582873984a561b64427fef8d4afa891f5b21d7`.
The preceding closed-carrier classification improvement is already present in
both versions and is not counted again.

```sh
git show 0aa12f48cf37c6128c5fe3eae8fd015feaf3b8a0:concierge/grantfox_carrier_census.py > /tmp/census-before.py
python work/throughput/carrier-url-scan-20261004/benchmark.py /tmp/census-before.py concierge/grantfox_carrier_census.py
```

`results.json` contains all raw timings, environment details, hashes and method
parameters from the executed run. The benchmark makes no network requests and
does not obtain or modify any actual bounty claim.
