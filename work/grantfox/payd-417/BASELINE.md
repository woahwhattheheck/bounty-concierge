# GrantFox source/provider baseline — Protocol-Guild/PayD #417

Operation: `GFOX3-20260919-payd-417/R-source-provider-baseline`  
Worker: ZZ-Sol-Kestrel-V41 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `af5c348e83033ed3340e589b68e8554f0303060e`

## Provider and issue state

- GitHub issue #417 is OPEN and unassigned.
- Public GrantFox listing resolves, shows **Assigned to: Unassigned**, and exposes the application route.
- GrantFox documents one application per user and direct-GitHub-comment publication.
- Two existing applicant comments are present; neither contains landed implementation evidence.
- Reward is only signaled by `Maybe Rewarded` / campaign labels. No fixed amount, award, or payment is verified.
- This packet is pre-assignment research only. No upstream implementation, application, wallet, or payment mutation is performed.

## Current source map

### `frontend/src/components/EmployeeList.tsx`
Blob: `735d5561e062187ad0121fc76f08817275fd88c0`

- 6 inputs + 1 select.
- 5 placeholder attributes.
- 0 `<label>`, 0 `htmlFor`, 0 input/select IDs, and 0 ARIA labels.
- The add-employee modal is therefore still placeholder-only for name/email/wallet/position/salary, and the status select has no programmatic label.
- Five sortable desktop `<th>` elements use `onClick` with no `tabIndex` / keyboard handler, so sorting is pointer-only.
- Existing action buttons are native buttons, but icon-only desktop edit/remove buttons rely on `title` instead of a deliberate accessible name.

### `frontend/src/pages/RevenueSplitDashboard.tsx`
Blob: `0ad565378fa66837d9d889a9c759d853e283243a`

- 2 inputs, 2 placeholders.
- 0 labels, `htmlFor`, input IDs, or ARIA labels.
- Both targeted fields remain programmatically unlabeled.

### `frontend/src/pages/AdminPanel.tsx`
Blob: `f006db147bc281b66ac6bc7993a6430830cd9220`

- 14 inputs and 14 visual `<label>` elements.
- 0 `htmlFor` associations and 0 ARIA label references; only 4 input IDs.
- The issue description's “placeholder-only” shorthand is not literally true for every AdminPanel field, but the core acceptance gap remains: labels are not programmatically associated with controls.

### `frontend/src/components/UpgradeConfirmModal.tsx`
Blob: `ae1a22ac7716b9789ce80ff3e97bd0616b72af37`

- No `role="dialog"`, no `aria-modal`, no modal-level keyboard handler, no focus-trap query, and no `tabIndex` contract in the current main implementation.
- It contains two clickable `div` surfaces and no `aria-live` region.
- The modal does include a small amount of ARIA elsewhere, but not the requested modal semantics/focus lifecycle.
- Current implementation contains async validation/simulation/execution states; any assigned repair should identify which status text needs a bounded `aria-live` announcement rather than marking the entire modal live.

## Active overlap that must be reused

Open upstream PR #611 (`feat: implement design system tokens, modal animations, and responsive layouts`) is **not** linked to #417, but it overlaps the modal acceptance surface:

- PR #611 remains OPEN.
- It introduces `frontend/src/components/AnimatedModal.tsx` blob `16bba4549d5275de5b7fdad68c68e9dc87789d2e`.
- That primitive contains `role="dialog"`, `aria-modal`, Escape-key handling, focus movement, and a tabbable-element query/focus trap.
- PR #611 changes `UpgradeConfirmModal.tsx` to blob `db30ed09c33e9e7888016b436d505085fd2bd85c`, which consumes `<AnimatedModal>`.
- Therefore an assigned #417 implementation must **not** independently re-invent the modal focus trap on current main. It should either wait for/rebase onto #611 or coordinate a compatible extraction of the same primitive.
- PR #611 does not close the form-label gaps in EmployeeList / RevenueSplitDashboard / AdminPanel and does not, by itself, cover all custom-control / async-status acceptance for #417.

## Acceptance-oriented continuation after assignment

1. Refresh #611 and #417 immediately before work; preserve one coherent implementation carrier.
2. Programmatically associate every targeted form label. Prefer explicit `htmlFor` + stable `id` (or design-system equivalent); do not rely on placeholder/title as the accessible name.
3. Convert sortable table headers to a keyboard-operable pattern. Prefer real `button` controls inside headers or implement the full keyboard/focus semantics if a custom role is retained.
4. Reuse `AnimatedModal` if #611 remains the canonical live modal carrier; verify initial focus, Tab/Shift+Tab wrap, Escape dismissal, and focus restoration to the invoking control.
5. Add `aria-live` only for meaningful asynchronous validation/simulation/execution transitions; avoid noisy announcements for static content.
6. Test by accessible roles/names and keyboard interaction, then run `npm run lint` and `npm run build`. The repository has Vitest installed but no package-level unit-test script in `frontend/package.json`; do not invent a green unit-test command without checking the current test harness.

## Application-ready note

I can take this as a focused accessibility pass rather than a visual redesign. Current main still has unassociated form controls in the three named surfaces and pointer-only sortable headers in EmployeeList. I also found open PR #611 already introducing an AnimatedModal focus-trap/Escape primitive and migrating UpgradeConfirmModal to it, so I would reuse or rebase that work instead of creating a competing modal implementation. After assignment I would finish the form/control gaps, verify keyboard focus/restore behavior and bounded async announcements, and run the repository's lint + TypeScript/Vite build on the exact PR head.

## Authority

This evidence is advisory only. It does not assign the issue, claim a reward, submit an application, modify upstream source, use a funded wallet/mainnet, or assert payment.
