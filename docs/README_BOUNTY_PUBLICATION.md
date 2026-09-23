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

## Publication and recovery

Only the sentinel-delimited section is regenerated. Titles are shortened before Markdown escaping, preserving table structure and literal text. Original index bytes are never rewritten by this command.

The publisher builds a complete UTF-8 replacement in a temporary file beside README, flushes its contents, preserves the existing file mode, and replaces README atomically. Input/marker errors, staging failures and detected intervening README edits leave the old README in place. Temporary staging files are removed on the ordinary error paths. A process killed before cleanup can leave a `.readme-bounties-*` staging file; it is not a published README.

The pre-replace content/identity comparison detects intervening changes but is not a filesystem compare-and-swap. Keep concurrent publishers serialized through the existing workflow or operator process. No distributed lock, new scheduled job, full power-loss durability guarantee, or automatic provider retry is added.

On a failure, correct the named input or use the existing collector to refresh the cache, then invoke the same publisher again. Do not replace a failed collection with fabricated empty rows or edit a timestamp merely to make it recent. A source merge alone does not refresh the hosted README.

## Scope and credit

This extends the existing collector/README work, including the index-publication composition delivered in #587. It is internal discovery maintenance on this owned fork, not an upstream bounty submission or earnings claim. It adds no test suite, fixture, receipt archive, dependency, workflow, wallet action, provider call, or financial decision.
