# Currency-partitioned opportunity ranking

`concierge.currency_partitioned_ranker` closes the native-RTC prioritization gap without inventing an exchange rate.

The canonical bounty qualification path can already authorize advertised USD rewards and native RTC rewards. The older `concierge.opportunity_ranker` remains intentionally USD-only for backward compatibility. This module adds a separate decision-support surface that applies the same canonical intake gate and skill matcher, then ranks candidates **inside their own currency**.

## Authority boundary

The ranker does not establish that a bounty is still available, earned, won, merged, or paid beyond the canonical intake disposition it consumes. Reward values remain advertised-reward evidence. Win probability and effort are operator estimates. The output is not accounting or settlement evidence.

Most importantly:

- USD is ranked only against USD.
- RTC is ranked only against RTC.
- there is no global USD-versus-RTC winner;
- no exchange rate, USD equivalent, or RTC equivalent is inferred;
- one canonical source appearing more than once is excluded globally, even if duplicate rows claim different currencies;
- an ACTIONABLE intake exposing zero or multiple reward currencies is excluded rather than guessed.

The top-level receipt pins `fx_conversion=false`, `cross_currency_ranking=false`, and `cash_claim=false`.

## CLI

Pass a bounded JSON request by path or `-` for stdin:

```bash
python -m concierge.currency_partitioned_ranker request.json
```

A request contains:

```json
{
  "candidates": [
    {
      "snapshot": {
        "listing_url": "https://github.com/example/project/issues/17",
        "reward_evidence_urls": [
          "https://github.com/example/project/issues/17"
        ],
        "title": "[BOUNTY: 30 RTC] Parser repair",
        "body": "Implement the parser.",
        "labels": ["bounty"],
        "attempt_count": 0,
        "canonical_audit": {
          "repo": "example/project",
          "number": 17,
          "issue_url": "https://github.com/example/project/issues/17",
          "issue_state": "open",
          "open_pr_count": 0,
          "stale_listing_signal": false,
          "search_truncated": false
        }
      },
      "estimated_effort_hours": "3",
      "estimated_win_probability": "0.5"
    }
  ],
  "skills": ["python"]
}
```

Exact decimal strings are recommended. Floats, booleans masquerading as numbers, non-finite values, pathological decimal representations, duplicate JSON keys, non-standard JSON numeric constants, non-positive effort, and probabilities outside `[0, 1]` fail closed.

## Output

The receipt has independent `USD` and `RTC` partitions. Rows expose generic currency-local fields such as `advertised_reward`, `estimated_expected_value`, and `estimated_ev_per_hour`, plus an explicit `currency`. Ranking within each partition is deterministic:

1. estimated EV/hour;
2. estimated expected value;
3. skill match;
4. advertised reward;
5. lower estimated effort;
6. canonical source URL.

There is intentionally no top-level ranked array because that shape could be misread as a cross-currency ordering.

## Relationship to realized unit economics

`concierge.realized_unit_economics` measures verified settled native RTC per operator hour from wallet-bound evidence and complete effort scope. This ranker does **not** turn those historical observations into a forecast and does not consume wallet history. The two surfaces can inform operator judgment without collapsing historical cash evidence into unearned future-revenue claims.
