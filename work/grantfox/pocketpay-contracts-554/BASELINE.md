# GrantFox stale-scope baseline — Axionvera/pocketpay-contracts #554

Operation: `GFOX-PKT-554-STALE-SCOPE-PARALLAX-Q7N4-20260919`
Worker: ZZ–Parallax-Q7N4 · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream head: `7988c6efec9a73162ed7d2fffb3b8b6ebd5a7b67`

## Disposition

**STALE_SCOPE / MAINTAINER_CLARIFICATION. Do not treat #554 as fresh implementation supply without a new maintainer-defined residual.**

Issue #554 asks for early-withdrawal, unauthorized, insufficient-balance, failed-state-invariance, successful-withdrawal, and documentation coverage. Current main already carries those behaviors and tests, largely through work merged after the issue opened.

Pinned evidence:
- `contracts/savings_vault/src/test/token_backed_withdrawals.rs` blob `7711a379cdfa9e9d23a178489acb3faf1ec3da34`: success path, matured lock, insufficient unlocked balance, early locked-fund rejection, authorization rejection.
- `contracts/savings_vault/src/test/withdraw_lock.rs` blob `f9e6a84c8693516a3fe02bf8156e99debe66b1b5`: matured success plus immature/nonexistent/repeated/wrong-user/unauthorized failures.
- `contracts/savings_vault/src/test/unauthorized_access.rs` blob `8467681fe4721b8986e435bce8cfc66939f1b02c`: unauthorized withdraw and withdraw_lock coverage.
- `contracts/savings_vault/src/test/token_transfer_rollback.rs` blob `1d907d6e7f4f18e5794b4868e668a89c84af6fb9`: failed withdrawal/withdraw_lock and SAC-transfer failures preserve balances, events, and lock fields.
- `docs/authorisation-rules.md` blob `919aaf1a8f902f9e1f4d65f14159cbee44decd56`.
- `docs/failure-mode-catalogue.md` blob `b6d6da00d734c2fd3ed88a4a6de3b3488d149c79`.
- Post-issue merged commit `5265b0a8cab969a35baad77863322a221fdc6f9d` explicitly hardened Savings Vault authorization-failure tests and reported 467 tests passing.

A residual documentation drift exists: parts of the failure-mode catalogue still mark SAC-transfer failures “Not tested” although current rollback tests now cover them. That is a docs-consistency cleanup, not evidence that #554's requested guard suite is missing.

No application was submitted and no upstream source was mutated by this worker.
