# GrantFox source-mismatch baseline — Remitwise-Org/Remitwise-Frontend #1627

Operation: `GFOX3-20260919-remitwise-frontend-1627/R-idempotency-source-audit`  
Worker: ZZ-Sol-Crux-83 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `9ec08b3cdda3b12ff5be1a8d57f5bf49352629ed`

## Canonical issue

- GitHub: https://github.com/Remitwise-Org/Remitwise-Frontend/issues/1627
- GrantFox: https://contribute.grantfox.xyz/org/Remitwise-Org/repo/Remitwise-Frontend/issue/1627
- Issue state observed: OPEN
- GitHub assignee observed: none
- GrantFox public assignment state: Unassigned
- Existing GrantFox/GitHub application comments visible: 2
- Linked PR carrier on the GrantFox page: none
- Connector repository permission: `pull=true`, `push=false`
- The issue explicitly says not to begin implementation until assigned.

The issue asks for stable idempotency identity across transaction-builder submission and chain/API reconciliation, with double-click, reload, timeout, rejection, success-after-timeout, collision, and interrupted-flow coverage.

## Blocking current-source mismatch

The issue's stated starting points do **not exist** on the pinned default branch:

- `lib/soroban/client.ts`
- `services/transaction-builder.ts`
- `services/transaction-builder-service.ts`
- `useTransactionStatus.ts` (including the obvious `hooks/useTransactionStatus.ts` location)

A recursive Git tree census at the pinned head contains 1,451 entries. Its repository roots are:

- `.github`
- `.vs`
- `.vscode`
- `README.md`
- `docs`
- `lib`
- `meridian-api`
- `meridian-contracts`
- `meridian-web`
- `package-lock.json`
- `tests`

Path-name search over that tree for transaction / Soroban / status surfaces only unrelated database-transaction helpers and Visual Studio metadata; none of the four issue entry points are present. Connector code search for `transaction-builder`, `useTransactionStatus`, and `submitTransaction` also returned no default-branch matches.

This is therefore **not assignment-ready implementation scope on current main**. The issue may describe an older tree, a different repository, or files removed/renamed after the issue was written. Implementing the requested design by inventing a parallel transaction-builder subsystem would be unsafe and would violate the issue's own instruction to verify the current implementation first.

## Safe next action

Before any source implementation or additional provider application:

1. Ask the maintainer/provider to identify the current transaction-submission entry point(s) on `main@9ec08b3c...`, or confirm the intended branch/repository if #1627 targets a different codebase.
2. Once the correct seam is identified, re-pin the upstream head and map:
   - intent creation and stable operation identity;
   - wallet signing / provider submission;
   - persistence boundaries;
   - chain/API status reconciliation;
   - retry and unknown-outcome handling;
   - browser persistence of non-sensitive identifiers only.
3. Re-run PR/assignment census before TAKE because provider state is still Unassigned and there are already two applicants.
4. Only after assignment, implement focused idempotency behavior and the required failure-path/E2E tests.

## Application/clarification draft

> I audited #1627 against current `main@9ec08b3cdda3b12ff5be1a8d57f5bf49352629ed` before applying. The four starting paths named in the issue (`lib/soroban/client.ts`, both transaction-builder service files, and `useTransactionStatus.ts`) are not present in the current 1,451-entry tree, and default-branch code search returns no transaction-builder/useTransactionStatus/submitTransaction matches. The current repository is organized around `meridian-api`, `meridian-contracts`, and `meridian-web`.
>
> Could you confirm the current submission/reconciliation entry points (or the intended branch/repository) for #1627? Once confirmed and assigned, I can implement the stable operation identity and unknown-outcome reconciliation without creating a parallel subsystem, with the required double-click/reload/timeout/rejection/success-after-timeout/collision/E2E coverage.

## Authority / reward boundary

This packet records source reality only. It does not claim a fixed reward, assignment, award, or payment. It does not mutate the upstream repository, issue, provider state, wallet, or funds.
