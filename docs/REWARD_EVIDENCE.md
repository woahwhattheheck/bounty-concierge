# Reward evidence in bounty discovery

The collector retains text from issue titles and bodies. A numeric match is not a confirmed reward, available assignment, eligibility decision, cash value, or payment to this claimant. A campaign pool or cap may be the first amount mentioned.

## Use the existing commands

```sh
concierge browse --evidence --limit 20
concierge browse --min-rtc 0.01 --max-rtc 100 --evidence
concierge browse --report --limit 20
concierge announce --json
```

`browse --evidence` adds the retained excerpt, other amount mentions, and complete source URL beneath each displayed row. It uses the same source read as ordinary browse, not another fetch. Text is displayed on one line with control characters made visible. The ordinary table also labels amounts as unconfirmed and preserves the exact RTC match rather than rounding it to one decimal place.

`--min-rtc` and `--max-rtc` filter numeric RTC mentions, not guaranteed per-claim payments. Unknown, non-RTC, and malformed evidence is excluded when either bound is supplied. Explicit matched `0 RTC` is numeric; a legacy `reward_rtc: 0` without evidence is ambiguous and is not treated as a zero reward. Omit both bounds to retain unknown candidates. Known numeric mentions sort descending, followed by unknown rows; equal values retain their incoming order. Negative or non-finite bounds, inverted ranges, and negative display limits are rejected before collection.

The numeric compatibility API still uses floats, including filters and sorting. Exact source text is retained for display; these filters are not exact-decimal accounting. Difficulty labels and the collector's legacy ranking heuristics are unchanged and do not establish compensation.

`--report` retains the existing object containing full rows, source states, counts, collection interval and rate-limit metadata. Plain `--json` retains its successful list shape. An incomplete read still exits 2: `--report` can show partial rows with their status, while plain `--json` does not emit a success-shaped list. `--evidence` changes text presentation only; JSON rows already include the evidence. A zero display limit does not mean collection found no work.

`announce` passes the full collected rows to the existing formatter, so the evidence is no longer discarded by a legacy five-field adapter. Short, medium and long previews use the same qualified amount text as the README. This command generates previews; it does not post them. It performs a live collection, including with its existing `--dry-run` flag; use the formatter directly with retained rows for an offline preview. This integration does not change the announcement dispatcher's authorization or behavior.

## Retained fields

The existing `reward_rtc` numeric field remains unchanged for legacy consumers. `reward_evidence` supplies:

- `status`: `matched` means a finite RTC text match; `unconfirmed_text` means another supported amount pattern; `no_match` means the parser found no supported pattern, not that the sponsor offers nothing.
- `amount_rtc`: the matched float, or null. It is not a confirmed payment.
- `exact_text`: the literal source match, retaining fractional precision.
- `excerpt`: bounded single-line context around the primary match.
- `mentions`: up to five additional amount mentions.
- `evidence_kind`: the existing `rtc_exact`, `unconfirmed_text`, or `no_match` classification.

For example, `0.025 RTC` remains `unconfirmed 0.025 RTC`, while a parser miss is `unknown (no amount match)`. A title mentioning a pool stays unconfirmed even when its number is large. Inspect the excerpt and live sponsor terms before selecting paid work.

## Shared helpers and compatibility

`reward_summary(row)` preserves exact RTC source text and accepts ordinary numeric values or the README reader's Decimal values. It rejects booleans, non-finite values and negative values as amounts. Missing or invalid evidence never falls back to an apparently confirmed legacy number.

`reward_filter_value(row)` retains its optional-float API; `reward_sort_key(row)` puts unknown values last for browse and announcement selection. `reward_context(row)` supplies bounded context for the existing browse view. The collector, index publisher, and README's legacy index ordering remain unchanged. The atomic publisher already retains complete row objects and their evidence.

This completes consumer integration on top of khongten124's extraction in PR #590 and the existing source-completion and literal-text publication work in #586 through #589 and #591. It adds no queue, external provider request path, dependency, test harness, claim, wallet or payment behavior.
