# Bounty economics gate

`concierge.bounty_economics_gate` encodes the swarm's scheduling floor as a
fail-closed receipt. It exists to stop cheap or unverified work from consuming
implementation, review, and finalization seats.

## Scheduling policy

Only **source-verified USD cash economics** can automatically enter an active
work bucket. The gate never converts RTC, points, tokens, crypto, or an advertised
token amount into USD. A separate authoritative source must establish the USD
cash basis before cash thresholds are applied.

| Verified economics | Disposition | Routing |
| --- | --- | --- |
| Entire verified range is **$50.00+** | `ACTIVE_50_PLUS` | `#bug-bounty` |
| Entire verified range is **$10.00-$49.99** | `PILE_10_49` | `#bounty-pile-10-49` |
| Entire verified range is **below $10.00** | `PRUNE_BELOW_10` | none |
| Range crosses one of those boundaries | `HOLD_RANGE_CROSSES_BUCKET` | economics verification only |
| Fixed/range USD amount exists but its USD basis is not verified | `HOLD_UNVERIFIED_CASH` | economics verification only |
| Unpriced or discretionary without a verified amount | `HOLD_UNPRICED` | economics verification only |
| Token/points/native units without a verified USD cash basis | `HOLD_TOKEN_ONLY` | economics verification only |
| Economics observation older than the caller's explicit freshness window | `HOLD_STALE_ECONOMICS` | economics verification only |

A range is never promoted optimistically from its maximum. For example, a
verified "$20-$100" offer crosses the $50 scheduling boundary and therefore
HOLDs until the economics are narrowed or explicitly promoted by the owner.

## Input

```json
{
  "schema": "bounty-economics-gate/v1",
  "opportunity_id": "owner/repo#123",
  "program": "Example bounty",
  "reward": {
    "kind": "FIXED",
    "source_ref": "provider:owner/repo#123",
    "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "usd_basis_verified": true,
    "min_usd_cents": 5000,
    "max_usd_cents": 5000,
    "native_currency": null,
    "native_amount": null,
    "observed_at": "2026-09-19T23:00:00Z"
  },
  "evaluated_at": "2026-09-19T23:05:00Z",
  "max_snapshot_age_seconds": 900
}
```

Kinds are `FIXED`, `RANGE`, `UNPRICED`, `DISCRETIONARY`, and
`TOKEN_ONLY`. `TOKEN_ONLY` requires the native currency and amount for honest
recordkeeping, but those values are never converted by this module.

The caller must also bind when the economics were observed, the evaluation time,
and an explicit freshness window (1 second through 7 days). Future evidence is
rejected; stale evidence HOLDs. This prevents an old $50+ listing from remaining
automatically active after its reward terms change.

## Verification and authority

The receipt retains normalized reward evidence and a SHA-256 semantic receipt.
`verify_economics_receipt()` reconstructs the original request and recompiles
the disposition; changing a PILE/HOLD/PRUNE receipt to ACTIVE and merely rehashing
the JSON does not verify.

This is an internal scheduling control only. Provider application, source writes,
submission, and payment/wallet authority are all hard false.

## CLI

```bash
python -m concierge.bounty_economics_gate economics.json --json
```

Exit code 0 means `ACTIVE_50_PLUS`. Every non-active disposition exits 2 so a
scheduler can fail closed instead of accidentally admitting cheap work.
