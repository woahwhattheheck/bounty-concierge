# Active claim portfolio v2

`concierge.active_claim_portfolio` is a fail-closed internal custody gate for paid-work opportunities. It prevents two workers from silently taking the same canonical GitHub issue, enforces worker/sponsor/total capacity, binds active work to an exact authority generation, and exposes stale custody for review.

## Security boundary

Only `compile_live_active_claim_portfolio()` may return `READY_FOR_INTERNAL_CLAIM`.

The live path accepts identity, worker/sponsor assignment, reward metadata, and an optional discovery URL. It does **not** accept qualification receipts, availability receipts, `observed_at`, or an `as_of` clock. It obtains current UTC itself, then independently calls:

- `revenue_intake.qualify_live_revenue_intake()` for canonical reward/provenance/competition qualification; and
- `bounty_availability.inspect_bounty_availability()` for a separate stable-generation availability read.

Either read failing or returning non-object data fails closed. Provider exception text is not copied into the receipt. GitHub `owner/repo` identity is case-folded before live reads, candidate grouping, generation binding, event lineage, and capacity accounting.

### Rate-limit stop within a compilation

A confirmed GitHub rate-limit failure stops further provider reads in the
current compilation. The live readers wrap Requests errors, so the portfolio
inspects chained `HTTPError` responses: HTTP 429, or HTTP 403 with
`X-RateLimit-Remaining: 0` or a valid `Retry-After` value. `Retry-After` accepts
delay seconds and HTTP dates. A 403 JSON object whose string `message`
contains `rate limit` also stops the compilation: GitHub secondary limits
can omit `Retry-After` while primary quota remains. Malformed/non-object
JSON, a non-string message, other statuses and exception wording alone
do not provide that evidence. The response is already in memory; no
additional request is made. See [GitHub rate-limit documentation](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api#exceeding-the-rate-limit).

The row that encounters the failure retains its read-failure reason and adds
`LIVE_PROVIDER_RATE_LIMITED`. Reads skipped after that point receive
`LIVE_QUALIFICATION_NOT_ATTEMPTED_RATE_LIMIT` or
`LIVE_AVAILABILITY_NOT_ATTEMPTED_RATE_LIMIT`. A qualification failure can skip
availability on the same row. Remaining candidates still appear in the receipt,
and completed or cached results are preserved.

The stop lasts only for that invocation. A later explicit compilation starts
fresh. There are no automatic retries, sleeps, or persistent stop state.
Ordinary HTTP 401/403/404 errors and transport failures continue to use the
existing per-row HOLD behavior without suppressing other candidates' reads.

Offline replay through real Requests responses and an in-memory HTTP adapter
reduced prepared requests from six to one for three candidates when the first
qualification read returned HTTP 429 or a header-confirmed rate-limit 403.
All three result rows remained present. Ordinary access and transport errors
produced identical full receipts before and after the change. Completed and
cached rows stayed intact, and a later invocation performed fresh reads. These
are prepared-request counts; the replay made no network or provider calls.

### Reward binding

Caller reward fields are descriptive input, not live authority. When the fresh revenue-intake result is actionable, the portfolio re-derives the one native advertised reward from the verifier-owned qualification signals and requires the candidate's `reward_currency` and `reward_minor` to match exactly before READY is possible.

- USD is bound in exact cents (`$90` => `reward_minor: 9000`).
- RTC remains a distinct native currency and is never converted to USD. The current custody schema stores integer RTC, so a fractional canonical RTC offer fails closed rather than being rounded.
- Native-unit conversion uses exact decimal tuple arithmetic. Process-wide decimal precision and rounding are caller state and cannot change the canonical reward.
- Missing/malformed reward signals, multiple native currencies, multiple distinct amounts, currency mismatch, or amount mismatch all produce a hold.

The exact normalized qualification receipt is retained inside the fail-closed reward-binding generation when a mismatch is detected. No raw issue/comment text is introduced: `revenue_intake` results are already safe-to-log normalized receipts.

The implementation keeps the previously reviewed v1 deterministic ledger engine in the private `_active_claim_portfolio_core` module. The public v2 adapter supplies only verifier-owned live evidence to that engine. The fixed neutral `observed_at` used at the private-core boundary is deliberate: current authority bytes, not wall time, define an opportunity generation; event age is measured from verifier-owned current UTC.

## Historical replay

`compile_replay_active_claim_portfolio(..., as_of=...)` and the legacy `compile_active_claim_portfolio` alias are audit-only. A clean unclaimed historical row becomes `REPLAY_ONLY_HOLD` with `HISTORICAL_REPLAY_NON_DISPATCH`; replay can never authorize new work. `verify_active_claim_portfolio_receipt()` deterministically regenerates replay receipts and rejects receipt or policy drift.

## CLI

Live current decision (no `--as-of` option exists):

```bash
python -m concierge.active_claim_portfolio live \
  --candidates candidates.json --events events.json --policy policy.json \
  --output receipt.json
```

Historical audit:

```bash
python -m concierge.active_claim_portfolio replay \
  --candidates candidates.json --events events.json --policy policy.json \
  --as-of 2026-09-14T23:45:00Z --output receipt.json
```

`compile` remains an alias for replay so old automation fails safe. `verify-replay` checks a replay receipt against exact inputs.

## Authority limits

A READY result is internal work-custody permission only. It is not a GitHub claim, sponsor contact, upstream submission, acceptance, payout, cash, or revenue assertion. The receipt records these limits explicitly, including `caller_reward_metadata_authoritative=false`.

Reward values remain per-opportunity metadata; currencies are never summed or converted.

## Reproduce secondary-limit request savings

Run `PYTHONPATH=. python examples/active_claim_secondary_limit_replay.py`
from the repository root. The example uses the actual portfolio, qualification,
availability and Requests code; only HTTP transport and the verifier clock
are replaced. No provider calls, claims or payout operations occur.

The 2026-10-04 before/after replay at parent `3c3f18bc1ee20c08dafb736316dbb6f57212cd77`
covers 12 scenarios. A body-only secondary-limit 403 reduces three-candidate
prepared requests from 6 to 1 (also with nonzero remaining quota), or from
6 to 2 when encountered by the availability reader. With 100 candidates,
the count drops from 200 to 1. All result rows and fail-closed decisions remain.
Eight control scenarios have identical full receipt digests before/after:
ordinary permission errors, invalid JSON shapes, other statuses, transport
failure and the already-supported header/429 throttle cases. This is a
deterministic prepared-request reduction, not measured fleet or network speed.

Source blob after repair: `66aefdf85a99e8cefdbed8c9990041d5fd85842f`.
Use `--observe` on the original source to obtain the baseline counts.
No loop, custody, retry, scheduling or persistent-throttle behavior changes.
