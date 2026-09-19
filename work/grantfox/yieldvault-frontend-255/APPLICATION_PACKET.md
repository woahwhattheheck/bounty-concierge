# YieldVault Frontend #255 — source-specific application packet

Use only after a fresh GrantFox/GitHub census confirms #255 remains unassigned and maintainers confirm whether the issue targets today's production forms or an intentional wizard migration.

## Draft

I'd like to take YieldVault-Org/YieldVault-Frontend #255. I audited current `main@f0ce8b799ded8f8e4cc158069d1940b1f822eb17` and found an important source drift in the issue wording: the live `/vault/:id` path currently renders `DepositForm` and `WithdrawForm` under `Tabs`; it does not render `DepositWizard` or `WithdrawWizard`, and `WizardDemo` is not in the current route table.

The production financial flows do have the accessibility gaps the issue is concerned about. Amount validation is visible but not bound to the input with `aria-invalid` / `aria-describedby`; transaction outcomes and material preview changes are not live-announced; and the deposit/withdraw tab switcher has only partial ARIA tab semantics (no roving focus, arrow/Home/End model, or tab↔tabpanel association). The reusable wizard similarly lacks step focus movement, validation focus, and step/outcome announcements.

I would first confirm whether you want #255 to improve the current production forms directly or to migrate production onto the wizard components. I would not spend the issue solely fixing currently-unrouted wizard UI while leaving the live financial path unchanged.

For either approved scope I would:
- extend the shared amount-input contract for error association instead of scattering one-off ARIA;
- implement keyboard-complete flow switching/step navigation;
- move focus predictably on transitions and failed validation;
- add polite, deduplicated announcements for balance/fee/share previews and assertive or status announcements for transaction failure/success;
- preserve native button semantics and busy/disabled behavior;
- if modal-hosted, complete the shared Modal focus trap + opener restoration instead of adding a wizard-only trap;
- add focused component + keyboard + focus + live-region + axe-style regressions using mocked/local data only.

One source hygiene issue also needs to be handled if the wizard files are touched: both financial wizard modules currently import `useAppContext` twice under the same binding. The repo's tsconfig includes JS under `src`, so I would run a clean build first and repair that minimal blocker if reproduced before attributing any failures to the accessibility change.

Validation would be clean `npm test` + `npm run build` plus the new accessibility assertions; no funded wallet or real transaction is needed for this work.

I'll wait for assignment and your scope confirmation before changing upstream source.
