# Offline supply routing: URL validation cost

## Delivered behavior

The acceptance gate still validates both URL fields. It now scans whitespace and
DEL with a compiled Unicode regular expression instead of a Python generator.
When `source_url` is an exact plain string equal to `issue_url`, the already
successful, stricter GitHub issue validation is reused. Different values and
non-`str` types still enter the original source-URL validator. No persistent
cache, network request, qualification rule, content scan, digest, clock,
freshness threshold, disposition or payment authority changed.

The full-routing profile did not justify a general batch-context refactor;
`bounty_supply.py` remains unchanged.

## Executed comparison

Run in the ChatGPT cloud Linux x86-64 harness on October 4, 2026, using Python
3.13.5. The source bundle is `woahwhattheheck/bounty-concierge` commit
`4f5bc01a9b6d6b91d5937d9743ed3608f9003ebc`, tree
`4190cab009045b659e0cdce00eb0b7c405ab0cbe`. Source transport reused existing
run **37199760399**, artifact **11302078522**; no export or validation runner
was created. The module baseline also matched main `441eaf72` before editing.

- Baseline gate Git blob: `6d2545111f9e80e203a7eb09d5af2bab8fa24f29`.
- Candidate gate Git blob: `a4a2f42bc1c7c26c890e4b61a5a743f12aef5191`.

The comparison imports both complete gate modules and the actual supply router.
It changes only the gate-function binding to select the source variant. Each
workload contains 2,000 synthetic issue snapshots; there is one warm-up and nine
alternating-order repetitions per variant. Every resulting routing row, count
and receipt digest is identical, and input snapshots remain unchanged.

| Workload | Before median | After median | Time reduction |
| --- | ---: | ---: | ---: |
| Short repository URLs | 0.238401 s | 0.226627 s | 4.94% |
| Long repository URLs | 0.266967 s | 0.230593 s | 13.62% |
| Mixed ACTIVE / MAYBE / HOLD / PRUNE | 0.224247 s | 0.207827 s | 7.32% |

For equal issue/source URLs, one direct instrumentation pass observed two strict
URL validations before and one afterward. The predicate comparison covers every
Python Unicode code point; complete-validator outputs/errors matched across
23 examples, and complete gate receipts/errors matched across 52 URL-field
examples, including string subclasses. These checks run inside the same small
measurement script; no general test suite or mandatory review gate was added.

The raw samples show shared-host timing outliers. These medians are local,
synthetic offline-routing observations, **not** measured fleet throughput,
provider rate-limit relief, live bounty availability or earnings. The retained
artifact expires October 11, 2026; the source pins and this report remain in Git.

## Reproduce

From a checkout with the source commit available:

```sh
git show 4f5bc01a9b6d6b91d5937d9743ed3608f9003ebc:concierge/bounty_acceptance_safety_gate.py > /tmp/url-scan-before.py
PYTHONPATH=. python examples/bounty_supply_url_scan.py \
  --baseline /tmp/url-scan-before.py \
  --candidate concierge/bounty_acceptance_safety_gate.py \
  --output /tmp/url-scan-results.json
```

The script makes no network requests and does not dispatch work. Compare the
recorded source blobs when interpreting a later run.
Raw observations: [bounty-supply-url-scan-20261004.json](bounty-supply-url-scan-20261004.json).
