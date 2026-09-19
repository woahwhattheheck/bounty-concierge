# GrantFox baseline — Stellar-kraal/stellar-kraal-frontend #26

Operation: `GFOX3-STELLAR-KRAAL-FRONTEND-26-R-20260919-SOLSTICE`  
Worker: ZZ-Solstice · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream: `Stellar-kraal/stellar-kraal-frontend@390605df2d60ee605bc366e10e79b460b6122a6a`  
Pinned tree: `98a623d78760930287f628f511639268f29b05cb`

## Provider / issue state

- GitHub issue: https://github.com/Stellar-kraal/stellar-kraal-frontend/issues/26
- GrantFox: https://contribute.grantfox.xyz/org/Stellar-kraal/repo/stellar-kraal-frontend/issue/26
- GitHub state at observation: **OPEN**, no assignee.
- Labels: `frontend`, `accessibility`, `Maybe Rewarded`, `GrantFox OSS`, `Third Campaign`.
- GitHub issue comments: 1, a generic application from `shobhamerabacha-star` dated 2026-09-15.
- Fresh open-PR census: **0 open PRs in the repository**, therefore no current #26 implementation carrier.
- Public GrantFox page at observation: **Assigned to → Unassigned** and exposes “Apply to this issue”.
- No fixed reward amount, award, acceptance, or payment was visible/verified.
- Connector permission on the upstream repository: `pull=true`, `push=false`.

The issue requests a full audit plus remediation, but the public provider state is unassigned. This packet therefore stops at current-source mapping and an assignment-ready plan; it does not mutate upstream source or claim provider assignment.

## Current-source accessibility map

### 1. Loan marketplace table — confirmed residual work

`app/investor/page.tsx` blob: `47cce301706a565a7df60ef28a1e334306324639`

The table is real and the issue's table-header gap is present:

- the seven header cells render as plain `<th>` elements with no `scope="col"`;
- appraised value and principal are rendered as visible dollar strings, and LTV as a visible percent string, but none of those cells has an explicit accessible label that binds the value to its unit/context;
- status badges contain literal text (`ACTIVE`, `REPAID`, `LIQUIDATED`), so the current investor status badges are **not** color-only even though color styling is also used;
- the rows themselves are not clickable/focusable in this snapshot. The only row action is a native `button` for active loans. Any future whole-row interaction should add explicit keyboard semantics rather than assuming the issue's “interactive row” description already matches current source.

Focused E2E coverage exists in `e2e/scenarios/02-browse-marketplace.spec.ts` blob `d0105b756be36192ba12b08ffb74fb07ac09af58`, but it checks visibility/content and a screenshot—not table semantics or axe results.

### 2. Farmer portfolio/status cards — partially compliant already

`components/AnimalCard.tsx` blob: `4e05a3e51d7eadd1e97d5b915673ba0047b5b375`

- verification status is rendered as visible text (`VERIFIED`, `PENDING`, `LOCKED`) in addition to color, so this surface is not color-only;
- value is rendered as `$<amount> USDC`, and weight/age include visible units;
- current portfolio E2E (`e2e/scenarios/04-portfolio-view.spec.ts`, blob `f0ea5a5a0350035ced66734b945eab28b90bb076`) asserts text/data only; it has no axe or keyboard/focus assertions.

### 3. Farmer registration modal — strong residual gap

`app/farmer/page.tsx` blob: `38feec2977188abb66edf4efadcc98ff017dc3ba`

The “Register Animal” overlay currently has:

- no `role="dialog"`;
- no `aria-modal="true"`;
- no `aria-labelledby`/accessible dialog title binding;
- no initial-focus policy;
- no focus trap;
- no Escape handler;
- no restoration of focus to `#btn-register-animal` after close.

This is a direct target for the issue's modal acceptance criterion.

### 4. Existing modal pattern is useful but incomplete

`components/MobileWalletModal.tsx` blob: `c265b380ad559eb5e37bc30f7460791ad3435b0c`

This component already supplies a better in-repo pattern:

- `role="dialog"`, `aria-modal="true"`, and `aria-labelledby`;
- initial focus on the dialog container;
- Escape-to-close;
- labeled close button and visible focus-ring classes.

However, it does **not** implement a real Tab-cycle focus trap and does **not** retain/restore the opener element on close. The assigned implementation should avoid copying those omissions.

### 5. axe/CI substrate exists, but coverage is too narrow

`package.json` blob: `71798fc65814b87d9a9d0bb22ff14e6a44258c21` already includes `@axe-core/playwright`.

`e2e/scenarios/09-mobile-wallet-qr.spec.ts` blob: `135cdd8612ec71e683991c12ebb8ccd73a6b8846` runs an axe WCAG 2.1 A/AA scan, but only against `[role="dialog"]` in the mobile-wallet scenario.

`.github/workflows/e2e.yml` blob: `750e769a5c36288c39221b11921467194faf8d65` runs Playwright on pushes/PRs, so axe is already indirectly part of CI for that one scenario. The missing work is route/surface coverage and a severity gate for the audited marketplace/portfolio/modal pages—not introducing axe from scratch.

### 6. Transaction-history scope is not represented in the current tree

The pinned recursive Git tree contains 50 blobs. No path contains `transaction` or `history`, and the user-facing route inventory is centered on `/investor` and `/farmer`.

That means “transaction history views” cannot be honestly mapped to an existing dedicated component/page in this snapshot. After assignment, the implementer should first confirm whether transaction history is expected to be added in this repo, represented by an unnamed existing surface, or tracked elsewhere; do not invent audit results for a view that is not present.

### 7. Required audit report does not exist yet

No `docs/accessibility/audit-report.md` exists at the pinned tree. Existing docs are unrelated (for example `docs/testing/e2e-guide.md`).

## Assignment-ready implementation plan

1. **Audit harness first**
   - Extend Playwright with route-level axe scans for `/investor` and `/farmer`, covering the populated table/cards and opened registration modal.
   - Fail on critical/serious violations and emit enough detail to reproduce findings.
   - Keep the existing mobile-wallet axe test, but expand modal assertions to focus behavior.

2. **Table semantics**
   - Add `scope="col"` to marketplace headers.
   - Bind currency/percentage cells to explicit accessible names or header relationships so units are announced reliably.
   - Preserve native button semantics for “Fund Loan”; do not make whole rows clickable unless keyboard activation/focus requirements are implemented and tested.

3. **Reusable modal focus behavior**
   - Refactor/extract a small reusable focus-management seam rather than fixing each overlay independently.
   - Add proper dialog labeling, initial focus, Tab/Shift+Tab trapping, Escape close, and opener-focus restoration to farmer registration.
   - Bring `MobileWalletModal` up to the same focus-trap/restore contract so there is one coherent accessibility convention.

4. **Status and portfolio semantics**
   - Preserve visible status text already present.
   - Add targeted accessible-name assertions for values/units rather than treating color-only status as a current defect where source already includes text.

5. **Evidence**
   - Create `docs/accessibility/audit-report.md` recording initial automated/manual findings, severity, exact affected surface, remediation, and post-fix state.
   - Perform one real NVDA or macOS VoiceOver pass after assignment; automated axe output is not a substitute for the manual acceptance criterion.
   - Record exact test/build commands and hosted CI state.

## Source-specific application draft

> Applying for #26 after auditing current `main@390605df2d60ee605bc366e10e79b460b6122a6a`.
>
> I found the issue is still materially open but the current source is more nuanced than the issue summary: `app/investor/page.tsx` has seven data-table headers without `scope`; marketplace E2E checks content/screenshots but no accessibility semantics; `app/farmer/page.tsx` has a registration overlay with no dialog role, focus trap, Escape handling, or focus restoration. The repo already has `@axe-core/playwright` and one WCAG 2.1 AA scan for `MobileWalletModal`, so I would extend that existing substrate rather than introduce a parallel framework. The mobile-wallet dialog itself already has role/label/Escape/initial-focus support but still lacks a real focus trap and opener restoration.
>
> Plan after assignment: (1) add route-level axe gates for the populated investor/farmer surfaces and opened dialogs; (2) fix table header/number-unit semantics; (3) implement one reusable dialog focus contract (trap + Escape + opener restoration) and apply it to both farmer registration and mobile-wallet flows; (4) preserve the status text that already prevents color-only communication; (5) document initial/fixed findings in `docs/accessibility/audit-report.md` and perform the required NVDA/VoiceOver manual pass. I will first confirm where the requested transaction-history view lives because no transaction/history path exists in the pinned 50-file tree.
>
> I will wait for provider/maintainer assignment before assignment-dependent source edits.

## Evidence hashes

- upstream commit: `390605df2d60ee605bc366e10e79b460b6122a6a`
- upstream tree: `98a623d78760930287f628f511639268f29b05cb`
- `app/investor/page.tsx`: `47cce301706a565a7df60ef28a1e334306324639`
- `app/farmer/page.tsx`: `38feec2977188abb66edf4efadcc98ff017dc3ba`
- `components/AnimalCard.tsx`: `4e05a3e51d7eadd1e97d5b915673ba0047b5b375`
- `components/MobileWalletModal.tsx`: `c265b380ad559eb5e37bc30f7460791ad3435b0c`
- `e2e/scenarios/02-browse-marketplace.spec.ts`: `d0105b756be36192ba12b08ffb74fb07ac09af58`
- `e2e/scenarios/04-portfolio-view.spec.ts`: `f0ea5a5a0350035ced66734b945eab28b90bb076`
- `e2e/scenarios/09-mobile-wallet-qr.spec.ts`: `135cdd8612ec71e683991c12ebb8ccd73a6b8846`
- `.github/workflows/e2e.yml`: `750e769a5c36288c39221b11921467194faf8d65`
- `package.json`: `71798fc65814b87d9a9d0bb22ff14e6a44258c21`

## Authority / publication fence

- Upstream GitHub App is read-only (`pull=true`, `push=false`).
- Public GrantFox state is unassigned at observation.
- No upstream source edit, issue-state change, provider assignment, reward claim, wallet/funds action, or payment action is performed by this packet.
- This baseline is reusable evidence; assigned implementation must refresh upstream head, issue/PR census, and provider assignment before coding.
