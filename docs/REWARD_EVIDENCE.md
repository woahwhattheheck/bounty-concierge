# Reward Evidence in Bounty Discovery

`concierge.reward_evidence` extracts and preserves reward evidence from bounty issue titles and bodies without declaring campaign pools, unconfirmed tokens, or non-RTC figures to be payable per-claim compensation.

## Overview

The legacy parser (`parse_reward`) extracts only the first finite numeric `RTC` mention, defaulting to `0.0` or `unknown` when none is found. `extract_reward_evidence` augments each discovered bounty with structured metadata while preserving the legacy numerical API:

- `status`: One of `matched` (finite RTC reward found), `unconfirmed_text` (dollar, euro, token, or general reward patterns), or `no_match` (no reward patterns identified).
- `amount_rtc`: Parsed float value for confirmed RTC amounts, or `None`.
- `exact_text`: The literal string matched in the text (e.g. `'150 RTC'`, `'$50'`).
- `excerpt`: A bounded single-line context excerpt around the primary match.
- `mentions`: Bounded list (up to 5) of additional amount or token mentions.
- `evidence_kind`: `'rtc_exact'`, `'unconfirmed_text'`, or `'no_match'`.

## Helper Functions

- `reward_summary(row)`: Generates a human-readable summary (e.g., `'150 RTC'`, `'unconfirmed ($50)'`, or `'no reward listed'`).
- `reward_filter_value(row)`: Extracts a numeric float value for reward filtering (`--min-rtc`/`--max-rtc`), returning `None` for unconfirmed or missing amounts.

## Renderers

`readme_sync.render_table` and `announcer.format_announcement` print `reward_summary` when a row has `reward_evidence`. That text stays an indexed mention: matched RTC text, `unconfirmed (...)`, or `no reward listed`. It is not per-claim compensation. Rows with no evidence object keep the previous numeric `indexed N RTC` display. Sort order still uses `reward_rtc`.

## Safe Integration Contract

- Legacy `reward_rtc` numerical fields and existing sorting/ranking behaviors are strictly preserved.
- Table and announcement renderers remain compatible with both legacy and evidence-augmented rows.
