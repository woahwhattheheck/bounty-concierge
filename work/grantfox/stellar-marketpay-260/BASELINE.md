# GrantFox baseline — Stellar-MarkeyPay/Stellar-MarketPay #260

Operation: `GFOX2-105-R-BASELINE-SOL47-20260919`
Worker: ZZ / Sol-47 · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream head: `42250890ecb5b76228675167f47452e54fb28977`

## Canonical issue

- GitHub: https://github.com/Stellar-MarkeyPay/Stellar-MarketPay/issues/260
- GrantFox: https://contribute.grantfox.xyz/org/Stellar-MarkeyPay/repo/Stellar-MarketPay/issue/260
- Observed state: OPEN / GrantFox Unassigned
- Existing comments: 3
- Matching open PR for #260: none surfaced
- Labels: `help wanted`, `Maybe Rewarded`, `GrantFox OSS`, `area: backend`, `area: frontend`, `complexity: XL`, `Third Campaign`, `epic`
- Reward boundary: no award, fixed amount, or payment is asserted.

The issue requests a design comment before implementation and reviewable phased PRs. This packet stops at source baseline + design.

## Existing billing primitives

This issue is partially absorbed by later work, so an implementation should extend current primitives instead of creating a parallel invoice subsystem.

Pinned evidence:
- `backend/src/services/timeTrackingService.js`: `e6c35ac01a90dfb2e28c6219c3c48046bdf00985` — current time-entry/time-invoice billing workflow
- `backend/src/db/schema.sql`: `dcd392bbdd47102c50b2b733d29201a551819ec1` — financial/audit tables including referral payouts
- `backend/src/services/referralService.js`: `bd9306612cd537026e2c63c955c51ab51ccb7f00` — fee/referral accounting
- `contracts/marketpay-contract/src/lib.rs`: `bc688fa7526454c5d84aa1a0ae398e1e8f4b68a9` — canonical `PLATFORM_FEE_BPS = 100` (1%)

Current main also has retainer-statement machinery. These are billing/approval records, not a complete immutable post-payment accounting-document layer satisfying #260.

## Residual gaps

Focused source search surfaced no historical fiat-rate snapshot field, no invoice accounting-document PDF renderer, and no #260 implementation carrier. Existing structured fee/referral arithmetic should be reused rather than independently recreated.

Tax identifiers, retention periods, and jurisdiction-sensitive document requirements are treated as configurable product requirements. This packet does not assert legal or tax sufficiency for any jurisdiction.

## Phased design after assignment

1. **Model:** add an immutable accounting-document snapshot around completed payment/release events. Snapshot issuer/recipient billing data, job/payment identity, tx hash, XLM amount, canonical fee/referral breakdown, document references, and FX quote/source/timestamp. Allocate per-issuer numbers transactionally according to product requirements.
2. **Generation:** issue from completed releases/payments; model partial releases/refunds with credit notes referencing the original document. Historical documents must never be recomputed from mutable current profiles, fee settings, or exchange rates.
3. **Rendering:** render localized PDF/CSV from the immutable snapshot behind an interface, including an on-chain verification reference.
4. **Summaries/export:** annual/date-range summaries and deterministic bulk export.
5. **Retention/tests:** configurable retention policy plus arithmetic regression tests against contract/spec fee and referral fixtures.

## Application basis

A source-specific application should explicitly acknowledge existing `time_invoices` and retainer statements and propose extending them with immutable transaction-time accounting snapshots, FX capture, credit-note linkage, rendering/export, and historical retrieval. It should not promise jurisdictional legal compliance without maintainer-supplied requirements.

## Provider publication status

GrantFox showed Unassigned with one application per user. This seat did **not** submit an application: the immediately preceding #385 flow failed to publish through the available authenticated browser path, so a second browser submission was intentionally not attempted.

No upstream implementation, assignment, provider application, payment, wallet, reward, or legal/tax conclusion is created by this packet.
