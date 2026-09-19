# Bounty cash admission

`concierge.bounty_cash_admission` is the fleet's **queue-routing** front door for bounty intake. It implements the owner floor without weakening the deeper paid-work / claim gates.

## Fixed routing policy

| Verified candidate-specific value | Disposition | Route |
| --- | --- | --- |
| USD/USDC nominal amount >= 50 | `ACTIVE` | `#bug-bounty` |
| USD/USDC nominal amount >= 10 and < 50 | `MAYBE_SAVE_UP` | `#bounty-pile-10-49` |
| USD/USDC nominal amount < 10 | `PRUNE` | none |
| unpriced / `Maybe Rewarded` / non-guaranteed amount | `HOLD` | none |
| arbitrary token/points denomination | `HOLD` | none; no FX conversion |

An `ACTIVE` receipt is **not** permission to claim or implement. The existing paid-work, availability, assignment, payout-route, effort/rate, and claim-authority gates still apply downstream.

## Evidence precedence

Reward observations are exact-target-bound. Every row carries a `target_url` that must equal the candidate's canonical source URL, so a reward cannot be transplanted from another issue.

Trusted reward authorities are `FIRST_PARTY` and `PROVIDER`. `OTHER` evidence is retained but cannot mint active value. Within trusted evidence:

1. issue-specific evidence outranks programme-wide evidence;
2. within a scope, first-party evidence outranks provider evidence;
3. conflicting rows at the strongest scope/authority fail closed.

This means an issue-specific sponsor classification of `$20` correctly overrides a generic programme table saying `$50+`. Likewise, an issue-specific `Maybe Rewarded` observation holds even if a lower-priority campaign page advertises a larger generic range.

## Availability

The router requires a fresh observed state. Closed work is pruned, work assigned to another contributor is held, unknown/stale availability is held, and only open-unassigned / open-assigned-to-us candidates proceed to value routing.

## USD and USDC

USD and explicitly denominated USDC are accepted nominally for queue routing because the sponsor has already stated the unit. The router does not convert RTC, points, arbitrary tokens, or any other currency into USD. It does not assert USDC redemption, cash settlement, receivables, or revenue.

## Example input

```json
{
  "schema": "bounty-cash-admission/v1",
  "as_of": "2026-09-19T23:00:00Z",
  "candidate": {
    "work_id": "acme-42",
    "canonical_source_url": "https://github.com/acme/widget/issues/42",
    "availability": {
      "state": "OPEN_UNASSIGNED",
      "observed_at": "2026-09-19T22:30:00Z",
      "source_url": "https://github.com/acme/widget/issues/42"
    },
    "reward_evidence": [
      {
        "scope": "ISSUE",
        "authority": "FIRST_PARTY",
        "kind": "EXPLICIT_AMOUNT",
        "guaranteed_for_candidate": true,
        "amount": "50",
        "currency": "USD",
        "observed_at": "2026-09-19T22:30:00Z",
        "source_url": "https://github.com/acme/widget/issues/42",
        "target_url": "https://github.com/acme/widget/issues/42"
      }
    ]
  }
}
```

Run:

```bash
python -m concierge.bounty_cash_admission request.json --json
```

Receipts are deterministic and SHA-256 self-committed. Their authority ceiling is queue routing only: no external claim, implementation, submission, payout, cash, or revenue authority is created.
