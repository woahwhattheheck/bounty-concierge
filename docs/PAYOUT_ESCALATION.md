# Payout escalation readiness

`concierge.payout_escalation` is the **timing/evidence bridge** between the live paid-work closeout queue and cash settlement. It does not recognize revenue or cash and it does not contact anyone.

The existing boundaries remain authoritative:

- `revenue_closeout` decides whether a paid PR is actually merged and free of current maintainer repair/response obligations. Its cash state stays `not_inferred`.
- `revenue_settlement` is the only rail in this repository that can turn explicitly bound **confirmed** wallet-history evidence into `partially_verified` or `verified_paid`.
- `payout_escalation` only answers: **given a merged RTC closeout item and exact operator-bound wallet evidence, is the observed payout timing still inside the repository's stated expectation, or is owner review now warranted?**

## Versioned timing policy

The rail pins policy `rustchain-bounty-payout-timing/2026-09-07/v1` to the repository's `docs/PAYOUT_GUIDE.md` blob `463ba80ccff616683aa9a85d691aadba74d25966`.

That policy carries two operational expectations from the guide's payout timeline:

- transfer initiation: within 24 hours of merge;
- pending confirmation: 24 hours after transfer initiation.

These are **operational expectations, not debt or contract authority**. Passing a deadline never proves sponsor acceptance, an amount owed, earned revenue, fiat value, or payment.

## Inputs

Run `revenue_closeout --json` first. Every item given to this rail must be `MERGED`, `currency=RTC`, and `cash_status=not_inferred`. The closeout receipt must also still be on one of the existing settlement routes (`route_settlement_followup` or `monitor_settlement`); a merged item with current maintainer repair/response work is refused rather than allowing payout timing to outrank that obligation. Canonical PR URL and exact lowercase 40-hex head identity are revalidated.

Wallet history must preserve provider wallet provenance, either as the canonical RustChain envelope:

```json
{"ok":true,"miner_id":"my-wallet","transactions":[],"total":0}
```

or an explicit offline capture:

```json
{"schema_version":1,"source":"rustchain_wallet_history","wallet":"my-wallet","items":[]}
```

Bindings are operator-owned attribution evidence. They must pin this exact policy version and wallet:

```json
{
  "schema_version": 1,
  "policy_version": "rustchain-bounty-payout-timing/2026-09-07/v1",
  "wallet": "my-wallet",
  "closeout_sha256": "<sha256-of-exact-closeout-json>",
  "history_capture_sha256": "<sha256-of-exact-history-capture-json>",
  "items": [
    {
      "repo": "Sponsor/project",
      "pr": 17,
      "history_sha256s": ["<wallet-bound-row-sha256>"]
    }
  ]
}
```

The two snapshot digests are deterministic drift fences: if the closeout or history capture changes after attribution, the binding is stale and compilation fails. They are self-integrity commitments, **not signatures or independent authentication**; an operator-supplied offline capture remains operator-supplied evidence.

One history row cannot support two PRs. Canonical incoming rows require a printable `tx_hash`; the same transaction identity cannot be represented twice under modified row bytes. Pending/failed rows require an initiation timestamp, must not be future-dated, and must not predate the merge they are attributed to. Bound evidence cannot exceed the advertised RTC amount.

## Actions

The output remains `cash_status=not_inferred` in every branch:

- `await_transfer_initiation_window` — no bound transfer evidence, still inside 24h after merge;
- `owner_review_missing_transfer` — no bound transfer evidence at/after that expectation;
- `monitor_pending_confirmation` — a bound transfer is pending/confirming and still inside its 24h confirmation expectation;
- `owner_review_pending_overdue` — bound pending evidence exceeded that expectation;
- `owner_review_failed_transfer` — bound transfer evidence explicitly reports failure;
- `run_revenue_settlement` — confirmed evidence exists; hand off to the existing cash-authority rail instead of reimplementing it.

A previously recorded `settlement_followup_url` is retained as context but **does not suppress an overdue classification**. The presence of a URL is not proof that a transfer was initiated.

## Production clock and authority ceiling

```bash
python -m concierge.payout_escalation closeout.json bindings.json --history history.json --json
```

The CLI takes current UTC from the verifier process and exposes no caller-chosen `--as-of`. The pure compiler accepts an injected aware datetime only for deterministic tests/replay.

The module performs no GitHub write, sponsor/maintainer contact, claim mutation, wallet mutation, transfer, provider write, invoice, accounting entry, or revenue recognition. `owner_review_*` means exactly that: route the evidence to a human owner for review. It is not permission to send a message.

Self-hashes in the receipt provide deterministic integrity/identity only. They do not authenticate an operator-supplied offline history capture.
