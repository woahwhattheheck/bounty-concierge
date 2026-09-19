# GrantFox source baseline — Stellar-kraal/stellar-kraal-frontend #26

Operation: `GFOX3-20260919-stellar-kraal-frontend-26-R`  
Worker: ZZ-Sol-Kestrel · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `390605df2d60ee605bc366e10e79b460b6122a6a`

## Provider / repository state

- Issue: https://github.com/Stellar-kraal/stellar-kraal-frontend/issues/26
- GrantFox: https://contribute.grantfox.xyz/org/Stellar-kraal/repo/stellar-kraal-frontend/issue/26
- GitHub: OPEN, unassigned, one applicant comment, zero open PRs surfaced.
- GrantFox: Unassigned; Apply visible; one application per user; one visible application.
- Labels: frontend, accessibility, Maybe Rewarded, GrantFox OSS, Third Campaign.
- Connector authority: pull=true, push=false.
- No fixed reward, award, payment, or assignment is verified.
- This is a pre-assignment source packet. Upstream implementation remains assignment-gated.

## Source truth

The issue is still materially open, but current main already contains useful accessibility substrate that an assigned implementation should reuse rather than duplicate.

### Marketplace table — real unresolved acceptance gap

`app/investor/page.tsx` blob `47cce301706a565a7df60ef28a1e334306324639` renders the loan marketplace table.

Observed:
- seven column headers are plain `<th>` elements without `scope="col"`;
- currency/LTV cells render visible units, but no explicit accessible labels bind the numeric value to its meaning/unit;
- status badges include visible status text, so they are not color-only, but need audit evidence rather than blanket replacement;
- funding is a button, not a row click, so the issue wording about keyboard-accessible interactive rows should be mapped to actual interactive controls instead of inventing row behavior.

A focused assigned repair should add semantic column headers and accessible numeric naming while preserving visible output and existing fund-button semantics.

### Farmer registration modal — unresolved focus-management gap

`app/farmer/page.tsx` blob `38feec2977188abb66edf4efadcc98ff017dc3ba` implements the Register Animal modal as a visual overlay only.

It currently has no dialog role / aria-modal contract, no focus trap, no Escape handler, no initial-focus policy, and no restoration to `#btn-register-animal` after close. This is a direct acceptance-criteria gap.

### Mobile wallet modal — partial positive precedent, not full closure

`components/MobileWalletModal.tsx` blob `c265b380ad559eb5e37bc30f7460791ad3435b0c` already has:
- `role="dialog"`, `aria-modal="true"`, labelled title;
- Escape-to-close;
- initial focus on the dialog;
- focus-ringed controls.

But it does **not** implement a true tab-cycle focus trap or restore focus to the launcher on close. It is useful substrate and a regression target, not proof that the issue is already solved.

### axe / E2E substrate already exists

`package.json` blob `71798fc65814b87d9a9d0bb22ff14e6a44258c21` already depends on `@axe-core/playwright`.

`e2e/scenarios/09-mobile-wallet-qr.spec.ts` blob `135cdd8612ec71e683991c12ebb8ccd73a6b8846` already runs an axe WCAG 2.1 A/AA scan on the wallet dialog.

`.github/workflows/e2e.yml` blob `750e769a5c36288c39221b11921467194faf8d65` runs the full Playwright suite as a gating step on PRs/main. Therefore the source-aligned solution is to extend existing Playwright/axe coverage to the audited financial views, not add a parallel accessibility runner.

Current `e2e/scenarios/04-portfolio-view.spec.ts` blob `f0ea5a5a0350035ced66734b945eab28b90bb076` checks visible content/status but performs no axe scan or keyboard/focus assertions.

## Assignment-time implementation contract

One coherent upstream PR should:

1. Add `scope="col"` to marketplace headers and accessible numeric naming that includes semantic units/currency without duplicating confusing screen-reader output.
2. Give the Register Animal modal a real dialog contract, bounded focus cycle, Escape handling, and focus restoration to its trigger.
3. Close the same trap/restoration gap in the existing Mobile Wallet modal without regressing its current role/label/Escape behavior.
4. Extend Playwright with axe scans for the actual financial/portfolio surfaces and focused keyboard assertions for modal opening, cycling, closing, and trigger restoration.
5. Add `docs/accessibility/audit-report.md` recording initial findings, severity, exact audited routes/components, remediation, automated results, and the required manual NVDA/VoiceOver findings.
6. Keep CI on the existing Playwright gate. Do not add a second competing accessibility pipeline unless evidence shows the existing gate cannot carry it.

## Required hostile / regression checks

- marketplace headers expose correct column semantics;
- numeric loan values expose currency/unit semantics without duplicate announcements;
- status remains understandable with CSS/color removed;
- Register Animal modal cannot tab out into page chrome;
- Escape closes and restores focus to `#btn-register-animal`;
- Mobile Wallet modal traps focus and restores to its launcher;
- axe on audited routes/dialog states has zero critical/serious findings;
- existing wallet/portfolio/funding E2E scenarios stay enabled;
- CI fails when an intentional critical/serious axe fixture is introduced;
- manual screen-reader evidence is documented rather than represented by automated axe output.

## Disposition

Source/provider research is complete. The issue is real and assignment-gated. Best next action is an authenticated, source-specific GrantFox application; implementation should begin only after durable assignment evidence. Reward remains possible/discretionary only.
