# Preview one retained bounty snapshot offline

Use this path when multiple workers need to inspect the same collected candidates without each issuing another GitHub collection. It reads a saved file and calls the existing announcement formatter. It does not fetch, refresh, claim, post, or access a wallet.

```sh
python -m concierge.announcer --index data/bounty_index.json
python -m concierge.announcer --index data/bounty_index.json --format long --limit 10
python -m concierge.announcer --index saved-browse-report.json --format medium
```

The first command prints one JSON object with `source` metadata and `previews.short`, `previews.medium`, and `previews.long`. Text formats print the source metadata above the selected preview, so the file's age and coverage do not disappear when someone reads a rendered table. The short preview string remains at most 280 Python characters; its surrounding command output is longer and is not a provider-ready post.

## Supported existing inputs

A cached index contains `bounties`, `total_count` and `updated_at`. A saved `concierge browse --report` object contains `rows`, `collected_count`, `filtered_count`, `displayed_count`, `complete` and `updated_at`, plus its existing source metadata. Bare `browse --json` row lists lack the necessary context and are not accepted here.

No live collection is performed by these commands. Reuse an existing file; obtaining a new `browse --report` is a separate, explicit network operation. The older `concierge announce` command is a different entrypoint and still performs its existing collection, even with its `--dry-run` option.

The file is bounded to 16 MiB. Duplicate JSON keys, non-finite JSON constants, malformed rows, inconsistent counts and invalid UTC timestamps produce an error before any preview is printed. The parser uses Decimal for fractional values and the existing rendering validators. It does not create a new schema version, proof registry or copy of the collector.

## Coverage, selection and exit status

`source.coverage` is `reported_complete`, `partial`, or `unspecified`. These are declarations from the retained file, not independently authenticated coverage. Older cache files without a `complete` field remain `unspecified`; a count matching the row list does not establish that every provider page was fetched.

For saved browse reports, `omitted_from_snapshot` is the difference between filtered rows and rows actually retained by the browse display limit. `rows_selected` is a separate preview subset controlled by `--limit` (0–1000, default 10). Selection preserves the file's existing order and does not rerank or alter its reward evidence. A complete collection can still have a limited display; zero selected rows never establishes an empty live queue.

Exit 0 means the preview was formatted and the input did not explicitly report an incomplete collection; unspecified coverage can also exit 0. Exit 2 with a preview means the saved report explicitly says `complete: false`. Exit 1 means input reading or formatting failed, with the error on stderr and no preview on stdout. Invalid command arguments use argparse's exit 2 without a preview. Consumers should read the JSON coverage field rather than infer live completeness from the exit code.

No freshness claim is made. `started_at` and `updated_at` are retained source timestamps; the command does not replace them with the current time or silently refresh old data. Confirm live sponsor terms and the claimant's payment route before undertaking paid work. Indexed amounts can be pools, caps or unsupported reward text, not promised per-claim compensation.

Output is stdout only. The input file is never rewritten and the dispatcher is never invoked. Shell redirection is available for an operator-chosen output path; avoid redirecting onto your input file.

This extends the existing literal-text formatter from #589 and reward-evidence consumers from #592. See `REWARD_EVIDENCE.md` for the meaning of exact text, numeric compatibility and unknown amounts.
