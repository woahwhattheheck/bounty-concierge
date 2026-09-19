# GrantFox baseline — ApexChainx/ApexChainx-Backend #366

Operation: `GFOX-APEX366-R-20260919-KEPLER-SOL`  
Worker: ZZ-Kepler-Sol · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `301ba96ba1044b678dc971ec07d69fe769b367a4`

## Canonical issue

- GitHub: https://github.com/ApexChainx/ApexChainx-Backend/issues/366
- GrantFox: https://contribute.grantfox.xyz/org/ApexChainx/repo/ApexChainx-Backend/issue/366
- State at observation: OPEN
- GitHub assignee: none
- Labels observed: `priority/medium`, `Maybe Rewarded`, `GrantFox OSS`, `Third Campaign`, `area/wallets`
- Existing issue comments observed before this packet: 2 application comments
- Matching PR census for issue number 366: no carrier surfaced in the fresh connector search
- Reward interpretation: `Maybe Rewarded` / campaign labels are eligibility signals only. No fixed amount, award, or payment is asserted here.

This issue explicitly asks contributors to wait for maintainer assignment before implementation. This packet therefore stops at current-source baseline + application-ready execution plan.

## Current-source finding

The reported defect still exists at the pinned head.

`app/services/wallet_registry.py` → `WalletRegistry.get_balance` does not query Stellar/Horizon. It synthesizes:

- XLM as `"1.0000000"` when `wallet.funded` is true, otherwise `"0.0000000"`.
- USDC as `"0.0000000"` whenever `wallet.trustline_ready` is true.
- USDC issuer as the literal string `"TEST_ISSUER"`.

The returned `WalletBalanceResponse` still includes `last_updated`, `cache_status`, `cache_ttl_seconds`, and `cached_at`, so the API shape looks freshness-aware even though the balance values themselves are not observed from the configured Stellar network.

Pinned blob evidence:

- `app/services/wallet_registry.py`: `51d5bc588295e26d6bb98574ff7ca975beb1beca`
- `app/models/wallet.py`: response model includes asset issuer and cache metadata.
- `app/core/config.py`: `9ba47162c1ed49287360d1d2650accb26fc020f3`; `STELLAR_NETWORK` exists and defaults to `testnet`.
- `tests/test_wallet_persistence.py`: `30d4bd73e5185616d67dba7409fbcfa4ea930aa9`; current persistence coverage does not exercise live/simulated balance semantics.
- `pyproject.toml`: `b1d785e0576b8776259f9723445e7c6c4c4cc9fe`; `httpx==0.28.1` is already a runtime dependency.

The newest upstream commit observed was `301ba96ba1044b678dc971ec07d69fe769b367a4`, which repairs unrelated backend startup/merge corruption. It does not resolve #366.

## Narrow implementation plan after assignment

1. **Network seam, not a framework rewrite**
   - Add one small config-driven balance-fetch boundary using existing `httpx`.
   - Map `STELLAR_NETWORK` to an explicit Horizon endpoint/config value.
   - Use bounded timeouts and surface network failures deterministically.

2. **Truthful response semantics**
   - Parse native XLM and issued assets from the real account response.
   - Never emit `TEST_ISSUER` on a non-simulated/live response.
   - Make degraded/offline behavior explicit instead of returning fabricated balances under freshness-looking metadata.
   - Preserve the existing response contract where possible; if a `simulated`/source field is required, document the compatibility impact.

3. **Tests**
   - Mock a Horizon/account response containing known XLM + USDC balances and assert exact propagation.
   - Mock network/unconfigured failure and assert the response is explicitly degraded/simulated or fails according to the selected contract.
   - Cover issuer propagation and ensure no `TEST_ISSUER` leaks into the live path.
   - Keep the existing wallet persistence tests green.

4. **Verification**
   - `pytest tests/test_wallet_persistence.py -q`
   - Add/run focused new balance tests.
   - Run the repository's typecheck/CI command(s) from the assigned implementation branch.
   - Report exact commands, exit codes, and any unrelated baseline failures.

## Application draft

> Applying for #366 after checking current `main` at `301ba96ba1044b678dc971ec07d69fe769b367a4`.
>
> Relevant fit: I traced the current wallet path before applying: `WalletRegistry.get_balance` still synthesizes XLM from the `funded` boolean and emits a literal `TEST_ISSUER` for USDC; the response model exposes cache/freshness metadata even though no network read occurs. The repo already depends on `httpx`, and `STELLAR_NETWORK` is configured, so this can stay focused without introducing a new framework.
>
> Approach:
> 1. Add one small, config-driven Stellar balance fetch seam with a bounded network timeout and deterministic parsing of native + issued balances; never return `TEST_ISSUER` on the live path.
> 2. Make degraded/offline behavior explicit in the response contract instead of silently presenting fabricated balances as fresh, while preserving existing callers as much as possible.
> 3. Add mocked live-fetch and network-failure tests around the existing wallet persistence/service path, including issuer propagation and the current funded/trustline compatibility cases.
> 4. Run the focused wallet tests plus the repository typecheck/CI commands and document any contract change for frontend consumers.
>
> Estimate: first draft within one working day after maintainer assignment. I will wait for assignment before implementing, per the issue workflow.

## Publication / authority notes

The linked GitHub App installation has read-only permission on the upstream repository. A native issue-comment attempt returned GitHub 403 `Resource not accessible by integration`; one retry was attempted and did not reach a successful provider write. That connector limitation is not evidence that the user's GitHub account itself lacks permission to comment.

No upstream code, issue state, wallet, funds, provider assignment, or reward state is mutated by this packet.
