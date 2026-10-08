# Sponsor acceptance liquidity

Run `python -m concierge.sponsor_acceptance_liquidity snapshot.json --json` before starting new paid issue implementations in a repository where our contributions are piling up.

Supply a UTC timestamped GitHub PR census. The input must include every currently open pull request and every closed pull request within a 7–90-day historical window, with author logins and merge/close timestamps. Set the two completeness flags false if GitHub pagination was not exhaustively read. Unknown or stale evidence cannot clear the gate.

Decisions: `HOLD_EVIDENCE` for incomplete/stale observations, `HOLD_NEW_BUILD` for 8+ outstanding original-author PRs with zero accepted merges or a severe rejection/backlog pattern, and `REVIEW_OTHER_GATES` otherwise.

Only *new* dispatch is evaluated. Existing authorships, PRs, claims, and compensation follow-up are unaffected. This is an advisory consistency receipt, not a statement of award eligibility, payout, or external authentication.

Input schema: `sponsor-acceptance-liquidity-input/v1`, fields `schema`, `repository`, `actor_login`, `observed_at`, `evaluated_at`, `window_start_at`, `max_age_seconds`, `all_open_prs`, `all_closed_prs_since_window`, `open_prs`, and `closed_prs`. Open PR rows contain `number`, `author_login`, `created_at`. Closed rows add `closed_at` and `merged_at` (or null).

Focused checks are in `tests/test_sponsor_acceptance_liquidity.py`.