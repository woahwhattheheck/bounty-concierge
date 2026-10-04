# Cached bounty README publication

`concierge/readme_sync.py` is the existing README publisher. It consumes `data/bounty_index.json`; it does not fetch providers, claim a bounty, submit work, register a wallet, or confirm payment. The existing collector and scheduled workflow remain the owners of cache refresh.

## Operator commands

From the repository root, display the generated section without changing README:

```sh
python -m concierge.readme_sync --require-fresh --top 10 --stdout
```

Publish that section between the existing unique `BOUNTY-TABLE-START` and `BOUNTY-TABLE-END` HTML comments:

```sh
python -m concierge.readme_sync --require-fresh --top 10
```

`--top 0` publishes the snapshot description and disclosure without candidate rows. Bad arguments return 2; malformed input or publication failure returns 1. A successful command returns 0. The library and CLI retain their historical opt-in freshness interface: callers that require freshness must pass `require_fresh=True` or `--require-fresh`.

## What the table means

Rows are **cached issue candidates**, not a verified work queue. They are ordered by indexed RTC amount descending, with unknown amounts after known amounts. Equal rewards have stable repository/issue/title/URL ordering. An absent reward is displayed as `unknown`; an explicit zero remains `0`. Decimal tokens are parsed without a float round trip and are not rounded to one decimal place for display. Very large or small exponents may remain in scientific notation.

An indexed amount can be a campaign pool, maximum, or extractor estimate. It is not necessarily a reward for one submission, cash value, an unexpired offer, an available assignment, or money owed to our account. The original issue remains the place to read the actual terms and deadlines. Confirm our eligible claimant and collection route in the existing work thread before starting paid work. This publisher creates no new approval process and confers no authority.

The existing 36-hour cache-age and five-minute future-skew limits are unchanged. With freshness enabled, an old snapshot produces a stale warning instead of rows. Invalid timestamps and structurally invalid snapshots stop publication. Missing/null `bounties` is not treated as an empty successful collection; an explicit empty array is supported. When supplied, `total_count` must equal the complete list length. Duplicate JSON keys, duplicate repository/issue identities, invalid row types and non-finite or negative reward values are rejected.

## Bounded display selection

The renderer validates the complete collection, then retains only the requested
best rows for display instead of sorting the complete collection twice. The
ranking and Markdown output are unchanged. Decimal ordering uses an exact sign
change rather than context-rounded unary arithmetic; a low-precision caller must
not collapse distinct indexed amounts. A zero display limit still validates all
input rows. This changes local selection work, not provider requests, freshness,
claim eligibility, or payment policy.

An offline Python 3.13.5 comparison of the complete `render_table` call used seven
alternating before/after pairs after warmup, with `top_n=10`. The retained input
was `data/bounty_index.json` at `4f5bc01a9b6d6b91d5937d9743ed3608f9003ebc`
(SHA-256 `9a1c190a7172554423a3511fce874dfed99039a64806c0babb05ded8a8c7e6c2`).
The larger inputs repeated its rows with unique synthetic issue identities;
they are not additional live offers.

| Input | Before median CPU ms | After median CPU ms |
|---|---:|---:|
| Retained 266 rows | 0.874002 | 0.644480 |
| Synthetic 10,000 rows | 18.834531 | 16.986840 |
| Synthetic 50,000 rows | 150.217890 | 93.130993 |

All compared tables were byte-identical. Separate direct calls covered limits
0, 1, 10, the full retained size and beyond it; precision-two Decimal ordering,
mixed numeric types and stable ties; and complete invalid/duplicate-row rejection
even with a zero limit. These are local renderer observations, not a whole-repo
suite, deployment, provider-quota reduction or measured fleet speedup.

The original renderer blob is `cbdf87fdc3b69f0e298901c2a45e7b1ad05f2e31`;
the measured replacement is `1a672b23fb7b56a6c5840e4f1e33c09824d3bfdc`.
To repeat the retained-input measurement from a checkout containing the change:

```sh
git show e15dddbb2a252991880c258c0ff77549b398ed90:concierge/readme_sync.py > /tmp/readme-before.py
git show 4f5bc01a9b6d6b91d5937d9743ed3608f9003ebc:data/bounty_index.json > /tmp/readme-index.json
PYTHONPATH="$PWD" python - <<'PY'
import importlib.util, json, statistics, time
from decimal import Decimal
from concierge import readme_sync as after
spec = importlib.util.spec_from_file_location("readme_before", "/tmp/readme-before.py")
before = importlib.util.module_from_spec(spec)
spec.loader.exec_module(before)
with open("/tmp/readme-index.json", encoding="utf-8") as stream:
    rows = json.load(stream, parse_float=Decimal)["bounties"]
assert before.render_table(rows, 10) == after.render_table(rows, 10)
samples = {"before": [], "after": []}
for iteration in range(7):
    pair = [("before", before), ("after", after)]
    for name, module in pair if iteration % 2 == 0 else pair[::-1]:
        start = time.process_time_ns()
        module.render_table(rows, 10)
        samples[name].append((time.process_time_ns() - start) / 1e6)
print({name: statistics.median(values) for name, values in samples.items()})
PY
```

## Publication and recovery

Only the sentinel-delimited section is regenerated. Titles are shortened before Markdown escaping, preserving table structure and literal text. Original index bytes are never rewritten by this command.

The publisher builds a complete UTF-8 replacement in a temporary file beside README, flushes its contents, preserves the existing file mode, and replaces README atomically. Input/marker errors, staging failures and detected intervening README edits leave the old README in place. Temporary staging files are removed on the ordinary error paths. A process killed before cleanup can leave a `.readme-bounties-*` staging file; it is not a published README.

The pre-replace content/identity comparison detects intervening changes but is not a filesystem compare-and-swap. Keep concurrent publishers serialized through the existing workflow or operator process. No distributed lock, new scheduled job, full power-loss durability guarantee, or automatic provider retry is added.

On a failure, correct the named input or use the existing collector to refresh the cache, then invoke the same publisher again. Do not replace a failed collection with fabricated empty rows or edit a timestamp merely to make it recent. A source merge alone does not refresh the hosted README.

## Scope and credit

This extends the existing collector/README work, including the index-publication composition delivered in #587. It is internal discovery maintenance on this owned fork, not an upstream bounty submission or earnings claim. It adds no test suite, fixture, receipt archive, dependency, workflow, wallet action, provider call, or financial decision.
