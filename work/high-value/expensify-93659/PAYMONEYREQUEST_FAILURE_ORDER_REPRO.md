# Expensify App #93659 — PayMoneyRequest stale-total failure-order repro

Status: **pre-assignment validation artifact; no implementation or payout claim**

- Sponsor issue: https://github.com/Expensify/App/issues/93659
- Advertised amount: $250 USD (linked Upwork route; assignment/provider acceptance required)
- Executable fork carrier: https://github.com/woahwhattheheck/App/pull/25
- Exact carrier head: `2a1e82a670f6536cd5a013646738eb396755ca4f`
- Upstream validation comment: https://github.com/Expensify/App/issues/93659#issuecomment-5746515157
- Test path: `tests/actions/IOUTest/PayMoneyRequestTest.ts`
- Source snapshot inspected: current `Expensify/App:main`; `PayMoneyRequest.ts` blob `1ee8496b191385ea50f282a2b62311306480b072`; `OnyxUpdates.ts` blob `62dfd4d54e05766bd1e946dcbe4757074e913321`.

## Proven ordering

`OnyxUpdates.applyHTTPSOnyxUpdates()` applies response `onyxData` first. For a non-200 response it then applies the request's `failureData`.

Current `PayMoneyRequest` failureData includes a MERGE for the IOU report whose value spreads the full pre-request `iouReport`. Therefore a server failure response that supplies a corrected `report.total` is followed by a client failure merge that reasserts the stale cached `total`.

## Deterministic regression

The fork carrier adds one test that:

1. seeds an IOU report with stale `total = 10000`;
2. mocks `PayMoneyRequest` to return `jsonCode: 400` plus response `onyxData` that MERGEs `total = 10001`;
3. invokes the real `payMoneyRequest()` action; and
4. asserts the final Onyx report keeps `10001`.

That expectation expresses the intended invariant. Current production ordering should make the test fail by restoring `10000`. A repair that narrows rollback to only fields changed optimistically should make this regression pass while preserving the backend amount-consent rejection.

No real payment, wallet, or production transaction is involved.

## Execution state

Creating the draft carrier triggered fork Actions. At publication time, Jest Unit Tests run `35479734310` and companion TS/lint jobs were queued; this packet does **not** claim execution green or red yet. The deterministic source-order proof above is independent of runner availability.

## Next contributor action

Use the existing Expensify proposal/assignment path. If assigned, run the exact mocked regression on the accepted implementation and require the corrected server total to survive failure rollback. Do not weaken the backend amount-mismatch consent guard and do not silently pay a changed amount without product/backend approval.
