# BountyHub original-contributor claim receipt (offline)

This module answers one narrow collection question: **does a currently observed BountyHub claim point to the original claimant and submitted PR, and is it pending, approved, rejected, or merely provider-marked paid?** It cannot register a new claim, contact a sponsor, check a bank account, or prove money arrived.

Use first-party snapshots retained by the provider reader plus independently read original GitHub issue/PR/comment and original author. The JSON input requires `bountyhub-claim-receipt/v1`, `actor_login`, `listing`, `funding`, `claim`, `pull_request`, `claim_request`, and `max_snapshot_age_seconds`. See `test_cli.py:BASE` as a **synthetic shape example only** (its IDs, money and timestamps are NOT real provider data).

```bash
python tools/bountyhub_claim_receipt/cli.py operator-snapshot.json > claim-review.json
# Historical, explicitly labelled replay only:
python tools/bountyhub_claim_receipt/cli.py operator-snapshot.json --as-of-utc 2026-10-09T09:00:00Z
```

Source observations must be fresh in the current live mode; historical `--as-of-utc` is for replay and does not permit claims. Recorded SHA-256 hashes are content digests, **not provider signatures**. The tool fails closed on mismatched PR or claimant identity, missing affirmative original-contributor compensation request, stale input, contradictory paid flags, and rejected claims. All pledge amounts are broken into provider-`PAID`, `PROMISED`, and `OTHER`; the advertised total cannot be substituted for escrow or contributor compensation. A claim `PAID` marker still yields `PROVIDER_PAID_MARKED_VERIFY_ACTUAL_SETTLEMENT`, and `cash_received_by_contributor` remains `UNVERIFIED` until separately reconciled with authorized settlement records.

Exit codes: `0` = no detected hold (not provider approval), `2` = HOLD or invalid snapshot. No network access, GitHub writes, payout transactions, provider authentication, or retries. Keep snapshots and any bank evidence in approved private custody; publish only scrubbed aggregate receipts. This implementation is intentionally separate from the existing BountyHub catalog, canonical viability, and MOVA modules.