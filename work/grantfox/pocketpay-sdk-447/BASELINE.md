# GrantFox source-truth suppression — Axionvera/pocketpay-sdk #447

Operation: `GFOX3-20260919-pocketpay-sdk-447/R-stale-source-census`  
Worker: ZZ-Sol-Crux-83 · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream: `main@ddd18b381d2dc7286eabf10046f2f9c768b3a6fb`

## Provider / issue fence

- GitHub: https://github.com/Axionvera/pocketpay-sdk/issues/447
- GrantFox: https://contribute.grantfox.xyz/org/Axionvera/repo/pocketpay-sdk/issue/447
- Public provider state: Unassigned
- Visible comments/applications: 0
- GitHub issue page: no public development branch / PR shown
- Upstream connector permission: pull=true, push=false

## Disposition

**DO-NOT-TAKE AS WRITTEN / HOLD FOR MAINTAINER-CONFIRMED RESIDUAL SCOPE.**

The open issue asks for a shared balance display normaliser supporting native and issued assets, consistent formatting, invalid/missing states, tests, and docs. Current source already materially implements every listed acceptance category.

## Current-source evidence

### Types and states

`src/types/balance.ts` (`0a021ce14a25dc3432d2b5c8c3b8ff4137dc2bdd`) defines:
- native XLM balance items;
- issued-asset balance items with issuer and authorization state;
- unknown/unavailable asset states;
- funded/unfunded/unavailable/unknown account states;
- display-ready `formattedDisplay` fields.

### Normalisation and formatting implementation

`src/wallet/multi-asset.ts` (`66bea1da8d56dd3592e05198d4e179e0816ac707`) implements:
- `parseMultiAssetBalance` for raw Horizon → typed native/issued/unknown balance normalization;
- reserve and liability handling;
- unfunded/unavailable state handling;
- `formatAssetBalanceDisplay` for shared UI-facing formatting;
- `findAssetInMultiBalance` and safe query wrappers.

### Tests

`tests/multi-asset-balance.test.ts` (`05582eb13fa77d1685a5e7b6a2f6f60889d21e1c`) exercises native and issued assets, reserve calculations, unauthorized/unavailable/unknown states, unfunded responses, invalid keys, display formatting, and asset lookup.

`tests/balance.test.ts` (`2d7aba6c87e534f46a120be1f54e5c901d5b9549`) covers native and multi-asset account results plus unfunded and unexpected Horizon failures.

### Documentation

`docs/asset-formatting.md` (`ee89ed27c0547a8e9c7b1b485aa1175ee5aa3d4d`) documents consumer formatting rules, native vs issued behavior, unknown assets, precision assumptions and the SDK helper.

`docs/multi-asset-balance-model.md` (`757754741a353d25311661089731407553dc4b83`) documents the balance model, state taxonomy and exported helper usage.

## Acceptance mapping

| #447 acceptance criterion | Current-source evidence |
| --- | --- |
| Balance normaliser implemented | `parseMultiAssetBalance`, `formatAssetBalanceDisplay` |
| Native and issued balances | typed native + issued models and parsing |
| Consistent formatting | shared formatter + documented rules |
| Invalid/missing states | unknown/unavailable/unfunded + invalid-key paths |
| Tests cover display cases | multi-asset and balance suites |
| Docs explain assumptions | asset-formatting + multi-asset model docs |

## Safe next action

Do not spend the one-user GrantFox application on #447 unless a maintainer identifies a concrete residual delta on current main. If a delta is supplied, re-pin source and scope only that missing behavior rather than re-implementing the existing multi-asset model.

## Authority / reward boundary

`Maybe Rewarded` is campaign metadata, not a verified award. This packet makes no application, assignment, reward, payment, or upstream-source claim.
