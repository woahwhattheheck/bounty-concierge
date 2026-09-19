# YieldVault Frontend #255 — deposit/withdraw accessibility source fence

Status: source-audited; provider/maintainer assignment required before upstream implementation.

## Target

- Upstream: `YieldVault-Org/YieldVault-Frontend`
- Issue: #255 — **a11y(frontend): make deposit and withdrawal wizards keyboard-complete and screen-reader safe**
- Source generation: `main@f0ce8b799ded8f8e4cc158069d1940b1f822eb17`
- GitHub: OPEN, unassigned, 1 comment, no open PR matching #255 at census
- GrantFox: Apply enabled, 1 application/user, Direct GitHub comment, Assigned to **Unassigned**, 1 prior applicant
- Same-day Slack exact YieldVault/#255 search before TAKE: 0 hits
- Upstream permission: pull=true, push=false
- Reward truth: MAYBE REWARDED / priority:high / Third Campaign; no payout is asserted.

No provider application, upstream mutation, funded-wallet action, transaction, credential, payment, or reward action was performed.

## The issue's current-source premise is partially stale

The production `/vault/:id` route does **not** render `DepositWizard` or `WithdrawWizard`.

`src/pages/VaultDetail.jsx` (blob `ee3756c2caeb716e5f345e88bb3e87ac0e89476f`) renders:
- `Tabs`
- `DepositForm`
- `WithdrawForm`

`src/App.jsx` (blob `2ca3da9d10e505d2cdd0f7ec4fb3d9ba094f8de6`) routes `/vault/:id` to that page and has no route for `WizardDemo`.

The repository does contain `DepositWizard.jsx`, `WithdrawWizard.jsx`, and reusable `FormWizard.jsx`, but the two financial wizards are not on the current production route. An assigned fix must not spend the issue entirely improving unreachable wizard components while leaving the live deposit/withdraw surfaces unchanged.

Maintainers should choose one explicit scope:
1. make the production forms meet #255 directly; or
2. intentionally migrate production to the wizard components, then make those wizards accessible and preserve financial behavior.

The smallest source-aligned interpretation is **production financial flows first**, with shared wizard fixes only where they are actually reused.

## Production flow gaps

### Deposit / withdraw amount fields

`src/components/DepositForm.tsx` (blob `e52209803f083e95d3ea38394bdd644f2024b39a`) and `WithdrawForm.tsx` (blob `807745121848993fc5f253cb76d2e79b39ad8b3b`) have visible labels, but:
- validation text is a plain `<p className="field-error">`;
- the amount input has no `aria-invalid`;
- it has no `aria-describedby` binding to the error/help text;
- transaction result text is not an ARIA live region;
- changing balance/position, previewed shares, and submit outcome has no announcement contract.

`src/components/AmountInput.tsx` (blob `b9e8e006923ae8408df78d716339e34576d0b501`) accepts `id` but no accessible-description/error props, so the cleanest fix is likely to extend the shared input contract instead of scattering raw ARIA attributes.

### Deposit / withdraw switching uses incomplete tab semantics

`src/components/Tabs.tsx` (blob `1628dc5a57db2fc73bd2b5c895fea9959224ae80`) applies `role=tablist`, `role=tab`, and `aria-selected`, but has no:
- roving `tabIndex`;
- Left/Right/Home/End keyboard handling;
- `aria-controls` / tabpanel id association;
- matching production `role=tabpanel` around the financial flow.

Click and normal Tab focus work, but this is not a complete ARIA tab interaction model.

### Existing route announcement is not enough

`src/components/RouteAnnouncer.jsx` (blob `213181db304cdfb43d01a901d319ad8492b0ecd3`) already has a polite atomic live region for route changes. That is a good reusable pattern, but deposit/withdraw tab changes, validation changes, fee/balance previews, and transaction outcomes do not change route and therefore are not announced by it.

Use dedicated flow-scoped status/error regions rather than overloading the global route announcer.

## Wizard-specific gaps if maintainers choose migration

`src/components/FormWizard.jsx` (blob `e30ad6e145cbdfbee5c49c3d0efd0733b6ee4bc3`) already has some useful semantics:
- tablist / tab / tabpanel roles;
- progressbar values;
- native buttons;
- Enter handling that intentionally avoids advancing from form controls.

But its step indicators are non-button `div role=tab` elements with no tab keyboard model or click behavior. Step changes do not move focus to a meaningful heading/panel target and are not announced. Validation failures set state but do not focus the invalid control or announce an error summary.

`test/components/FormWizard.test.jsx` (blob `0d9971afe05f8b0b4759a82d7fac1715f84019c6`) covers basic step navigation and Enter behavior, but no focus movement, ARIA association, keyboard tab navigation, live announcement, or axe-style assertions.

### Source hygiene blocker on the financial wizard files

Both:
- `src/components/DepositWizard.jsx` (blob `ea4344e711caf8cc4a1e7e232dd747c4cdb52602`)
- `src/components/WithdrawWizard.jsx` (blob `f1525ebcb3ec85c905567fe437317efa2fd71180`)

declare the same `useAppContext` import twice in the module.

`tsconfig.json` (blob `f7a34217b54cf1d04f46806869b32551ea490af0`) has `allowJs: true` and includes all of `src`, so this duplicate binding is a source-level compile/typecheck hazard even though the wizards are not routed. There were no workflow runs returned for the pinned upstream commit, so this packet does **not** claim current main is CI-green. Assigned work should run the exact clean build before and after changes and fix this minimal blocker if reproduced.

## Modal baseline

`src/components/Modal.tsx` (blob `1d7715fd77686c4b9e9ad437a4110f3eea5bbab0`) has `role=dialog`, `aria-modal=true`, Escape close, outside-click close, and a labelled close button. It does **not** implement a focus trap, initial-focus policy, or focus restoration to the opener.

If deposit/withdraw flows are or become modal-hosted, #255's dialog acceptance should use this shared component rather than adding one-off wizard trapping.

## Test/tooling reality

`package.json` (blob `c30544e7574b0476e6da97928b1c23118fb85d32`) has Vitest + Testing Library but no `@axe-core/react`, `jest-axe`, `@axe-core/playwright`, or user-event package.

CI `.github/workflows/ci.yml` (blob `e0de7e895eadefcae74e0601edbd35341cb1f1bd`) runs:
- `npm install --no-audit --no-fund`
- `npm test`
- `npm run build`

There is no dedicated accessibility job. The issue-required axe-style validation therefore needs a deliberately added test dependency/seam; do not claim existing CI already provides it.

## Acceptance contract

An assigned implementation should prove the **live production deposit + withdrawal paths** satisfy:

1. **Input/error association**
   - every amount field has a programmatic label;
   - invalid state uses `aria-invalid=true`;
   - exact validation text is bound by `aria-describedby` (or equivalent);
   - invalid submit moves focus to the first invalid field and announces the error without duplicate chatter.

2. **Keyboard completeness**
   - deposit/withdraw tabs implement the ARIA tabs keyboard pattern or are simplified to a semantic control with an equally clear interaction contract;
   - MAX, amount, submit, retry/close/back controls are reachable and operable without pointer;
   - disabled/busy state cannot strand focus.

3. **State announcements**
   - flow selection/step changes are announced once;
   - material balance/position/fee/share-preview changes use a polite, debounced/atomic strategy;
   - processing state is announced;
   - success/failure is announced once with an appropriate status/alert priority;
   - no live region re-announces the entire financial summary on every keystroke.

4. **Focus**
   - on flow/step transition, focus lands on the new heading or first meaningful control according to a documented rule;
   - failed validation focuses the invalid field;
   - if a modal is involved, focus is trapped while open and restored to the opener on close.

5. **Regression evidence**
   - component tests for both deposit and withdrawal;
   - keyboard tests using realistic user interaction;
   - focus assertions;
   - live-region assertions;
   - axe-style automated checks;
   - existing tests retained;
   - clean `npm test` and `npm run build`.

Use mocked/local data only. Do not use a funded wallet or real financial transaction for accessibility validation.

## Assignment fence

Immediately before implementation, re-census GrantFox assignment, issue comments, current upstream main, and issue-linked PRs. Confirm with maintainers whether #255 targets the current production forms or a deliberate migration to the currently-unrouted financial wizard components.
