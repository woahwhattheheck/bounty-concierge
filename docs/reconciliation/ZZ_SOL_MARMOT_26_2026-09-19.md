# ZZ-Sol-Marmot-26 — GrantFox work/value reconciliation (2026-09-19)

Model: GPT-5.6 Sol

## Current provider payment fence

Independent provider readback on 2026-09-19:

- `GET https://api.grantfox.xyz/api/campaigns?status=ACTIVE` returned an empty JSON array: `[]`.
- GrantFox Tier System documentation says tiers do not block applications, but paid-issue caps are per Campaign: 3 / 5 / 7 / 10 / 15 / 20+ depending on tier.
- The same documentation says applying consumes an Issue Cap slot until assignment, and paid issue caps are Campaign-scoped.
- GrantFox PR-linking documentation requires `Closes #ISSUE_NUMBER` in the upstream PR description for automatic issue-to-PR recognition.

This seat did not establish the owner's current tier, any active campaign containing the audited issues, an approved reward, an assignment, or a payment record. Therefore the source packets below are **research/readiness evidence only** and must not be counted as earned or payable GrantFox revenue.

## Durable source packets from this seat

### MentorsMind #1126 — recovery acceptance mismatch
- Carrier: `evidence/grantfox/mentorsmind-1126-recovery-baseline-20260919.md`
- Direct-main commit: `4e830043f772835fdaf5807b2a911111203305a3`
- Upstream pin: `MentorsMind/MentorsMind-Contract@e90e16fc3a78122949ce63af7308320c59acb112`
- Main finding: recovery helper returns rollback intent but has no explicit snapshot/restore mechanism.
- Later swarm refresh exposed a canonical duplicate carrier already merged by another seat; retain this only as corroborating evidence.

### Stellar-kraal #109 — serializer/fingerprint tests
- Carrier: `evidence/grantfox/stellar-kraal-109-stable-stringify-baseline-20260919.md`
- Direct-main commit: `37762f69d2a79e46dcc4757bfbfff3c78e200e9e`
- Upstream pin: `Stellar-kraal/stellar-kraal-contract@e57cc72c85c58b544af2fd698f569d8a149dd917`
- Main finding: 12-case stable-string/fingerprint regression matrix; no production API widening required.

### MentorsMind #1125 — grants pagination schema gap
- Carrier: `evidence/grantfox/mentorsmind-1125-grants-pagination-source-map-20260919.md`
- Direct-main commit: `387f4713385ac5da1d0973ce7100e5e3893b916a`
- Upstream pin: `MentorsMind/MentorsMind-Contract@e90e16fc3a78122949ce63af7308320c59acb112`
- Main finding: current grants storage has no enumerable GrantRecord/global grant-ID schema; issue implies a new persistence contract, not only view functions.

### MentorsMind #1132 — session pagination freshness
- Carrier: `evidence/grantfox/mentorsmind-1132-session-oracle-pagination-20260919.md`
- Direct-main commit: `a92008415c93fdb1be288ad8266818d098a310fb`
- Upstream pin: `MentorsMind/MentorsMind-Contract@e90e16fc3a78122949ce63af7308320c59acb112`
- Main finding: index session IDs/symbols, not copied mutable OracleSession values, or paginated state goes stale after completion/dispute/timeout.

## Slack-only source handoffs

### MentorsMind #1133 — staking admin-event canonicalization
- Public Slack receipt: `#bug-bounty` ts `1789858919.433099`
- Main finding: proposal already emits through legacy 2-topic layout; accept/cancel emit nothing; shared owns proposed/accepted payloads and canonical staking emitter but no shared cancelled payload type.
- GitHub carrier publication was attempted twice and blocked by connector safety checks; no carrier commit exists.

### MentorsMind #1134 — abuse-surge semantics mismatch
- Public Slack progress receipt: `#bug-bounty` ts `1789858771.033219`
- Main finding: current helper is an 80%-failed-request-ratio detector and current registry path blocks on detection; provider text describes advisory rapid-creation monitoring. Feeding session count into the helper's failed-request slot would create false positives.
- GitHub carrier publication was attempted twice and blocked by connector safety checks; no carrier commit exists.

## Value disposition

Under Bryce's 2026-09-19 value-priority directive:
- do not extend these unpriced/unassigned research lanes as new micro-bounty labor;
- preserve the source evidence already landed;
- do not represent internal carrier merges as upstream delivery, provider assignment, approved reward, or cash;
- resume implementation only if a current substantial reward/program route and assignment authority are established;
- prioritize collection of substantial already-delivered work and priced/eligible opportunities.
