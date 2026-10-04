# Bounded FAQ-key normalization reuse

The fuzzy matcher normalizes each FAQ key in both its exact and topic-overlap passes. It now reuses normalized ordinary-string keys in a 256-entry LRU, admitting only keys of at most 512 characters. Long keys and string subclasses keep direct normalization. Questions, answers, stop-word sets and FAQ mappings are not cached. Exact-match precedence, insertion-order ties, live answer/key edits, BountyHub routing and the independent paragraph cache remain unchanged.

## Measured local result

Python 3.13.5, Linux x86-64, 2026-10-04. Complete production modules were loaded with importlib, not copied matcher functions. Each workload used 500 warm-up calls per variant, followed by seven alternating before/after pairs of 2,000 calls. Times are median microseconds per `fuzzy_match` call; speedups are ratios of medians.

| Workload | Before µs | After µs | Ratio |
| --- | ---: | ---: | ---: |
| Built-in fuzzy query | 90.596 | 20.817 | 4.35× |
| Built-in late exact query | 35.323 | 4.076 | 8.67× |
| BountyHub fuzzy query | 31.985 | 9.507 | 3.36× |

Baseline blob: `f7ab72b1ea2e7c0465b91775f3284ce6335cef82`, present at base `bff9f43b7a0c99ba101b4c57b1281737c1fceb91`. Candidate blob: `aa80462d84083fd9a700d209222c4e059ec76854`. Focused local comparisons preserved outputs, exact precedence, Unicode, tie order, live mapping and stop-word changes, bounded-cache size, long-key bypass, string-subclass normalization calls and uncached questions. No repository test tree, hosted workflow, dependency change, provider request, model call or owner-machine operation was added or run. These are warm in-process matcher measurements, not end-to-end CLI, rate-limit, network, revenue or cold-cache speedup claims.

## Replay the timing comparison

Run from a checkout with its ordinary runtime dependencies. This reads local modules only:

```sh
git show bff9f43b7a0c99ba101b4c57b1281737c1fceb91:concierge/faq_engine.py > /tmp/faq-key-before.py
PYTHONPATH=. python - <<'PY'
import importlib.util, statistics, time

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

modules = [load('before', '/tmp/faq-key-before.py'), load('after', 'concierge/faq_engine.py')]
workloads = [('built-in fuzzy', 'how do bounty payouts reach a wallet?', None),
             ('built-in exact late', 'security bounties', None),
             ('BountyHub fuzzy', 'how do I receive payment?', modules[0]._BOUNTYHUB_FAQ_ENTRIES)]
for name, query, entries in workloads:
    expected = modules[0].fuzzy_match(query, entries)
    assert modules[1].fuzzy_match(query, entries) == expected
    for module in modules:
        for _ in range(500):
            module.fuzzy_match(query, entries)
    samples = [[], []]
    for pair in range(7):
        for index in ([0, 1] if pair % 2 == 0 else [1, 0]):
            start = time.perf_counter_ns()
            for _ in range(2000):
                result = modules[index].fuzzy_match(query, entries)
            samples[index].append((time.perf_counter_ns() - start) / 2000 / 1000)
            assert result == expected
    medians = [statistics.median(values) for values in samples]
    print(name, 'before/after us:', medians, 'ratio:', medians[0] / medians[1], 'raw:', samples)
PY
```

Recorded raw pairs, each `[before_us, after_us]`:

```json
{"built-in fuzzy":[[90.2587565,20.5724255],[90.5960395,20.982437],[92.494225,20.8172825],[90.9742685,20.2937685],[91.270301,21.362123],[89.38181,22.0087585],[90.226904,20.130262]],"built-in exact late":[[35.76577,4.098718],[35.3023245,3.886709],[35.1609305,4.0228405],[34.476484,4.172221],[35.323055,3.9199485],[36.0323135,4.0763145],[36.6326455,4.9753485]],"BountyHub fuzzy":[[31.9847215,8.923904],[31.4894445,9.7213125],[32.800597,9.5289035],[31.54419,9.4589545],[32.3555645,9.170283],[31.7438905,9.507401],[32.1289345,9.989083]]}
```
