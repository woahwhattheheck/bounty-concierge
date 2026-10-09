# GrantFox OSS: points are not settled money

Internal cash-truth guard for GrantFox OSS awards. For **existing submitted/merged work only**, gather canonical first-party issue/PR records, bot points receipts, specific monetary approvals, and a released Trustless Work escrow receipt *independently*. Run `python tools/grantfox_oss_cash_guard.py examples/quittance_20261009.json`. No platform login, payments, GitHub comments, or background polling.

The official [GrantFox OSS process](https://grantfox.xyz/open-source) says accepted work receives an escrow USDC reward **when applicable**, not for every merged issue; its [analytics page](https://analytics.grantfox.xyz/) independently lists released cash transfers and excludes test escrows. An OSS `Maybe Rewarded` label, merge, or `FoxPoints` bot comment is a technical/reputation event, not a USDC transfer or even an approved cash award. [Current Opire terms](https://docs.opire.dev/rewards/pricing) likewise require direct creator payment, not bot auto-pay. Do not infer a numeric USD debt from points.

Decision categories:
- `POINTS_ONLY_CASH_AWARD_UNVERIFIED`: preserve original PR and ask for an **itemized original-terms award decision** in the existing thread; do not repeat a pending request.
- `CASH_AWARD_RECORDED_NOT_PAID`: provider cash award exists, but no settlement record. Collection remains outstanding.
- `PROVIDER_RELEASE_RECORDED`: operator supplied a specific settlement receipt with explicit payee and sponsor verification; **the offline tool cannot independently fetch or authenticate that receipt**. Confirm live first-party ledger and account before counting cash received.

All statuses keep `new_unpaid_builds_allowed=false`; only the separate active-sponsor/same-payer completed-paid-merge eligibility gate can permit new work. This guard cannot accuse a maintainer of fraud, declare a contractual due payment, waive an existing claim, or initiate a payout. A missed requested response date is not in itself a proved contractual default.

## Quittance-Labs / Quittance0 live example (October 9, 2026)

- Sponsor issue [#443](https://github.com/Quittance-Labs/Quittance0/issues/443#issuecomment-5971364152) has GrantFox OSS bot acknowledging original @woahwhattheheck PR #574 and reporting **35 FoxPoints for that contribution, 350 cumulative FoxPoints**.
- [Consolidated original-contributor request](https://github.com/Quittance-Labs/Quittance0/issues/443#issuecomment-6073925279) names ten merged PRs: #562, #564, #565, #567, #568, #569, #570, #572, #573, #574; asks for itemized cash eligibility/payment decision by October 13. That is a requested response deadline, **not proven contractually binding**.
- No provider-approved USDC award amount or released USDC transfer receipt was present in these fetched GitHub records. Classification: `POINTS_ONLY_CASH_AWARD_UNVERIFIED`; preserve the claim, hold new unpaid builds, and check the *existing* request for a reply after its date rather than create ten duplicates.

Field guide: `points.cumulative_awarded` is an integer; no points-to-money rate exists. `cash.approved_award_usdc` requires a specific GrantFox cash award record. `cash.released_payment_usdc` requires a specific escrow release page, payee/sponsor identity checks and a dated first-party readback. All operator-provided evidence is still subject to independent verification; don't convert the output to receivables without it. Strict URLs reject query parameters and fragments except the canonical GitHub comment permalink suffix.
