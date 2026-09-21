# Tenstorrent tt-metal #53787 — log_sigmoid host acceptance oracle

Status: independent host/reference support for an **assigned** $5,000 implementation lane. This packet does **not** claim issue assignment, device execution, silicon accuracy, performance, implementation ownership, bounty entitlement, merge acceptance, or payout.

## Exact source fence

- Canonical issue: `tenstorrent/tt-metal#53787` — `[Bounty $5k] ttnn.log_sigmoid: 0.91% peak fp32 error and a missing x <= -4 branch`.
- Issue state at capture: OPEN; labels include `bounty` and `bounty_difficulty/hard`; assignee `kanapitsas`.
- Upstream `main`: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`.
- Wormhole header blob: `cdc5ad762c24219187ad8e6babe57a2d261474ff`.
- Blackhole header blob: `e2ca2e3e875ffcd78f1031df5cfdf4de59b9e0a9`.
- Compute kernel blob: `5e6522f035fc2856e805997a678dbfa63a7735f4`.
- Nightly sweep blob: `4653c5028a3df72684b11d87cbcd3b29bd4e9285`.

Both checked-in architecture headers still initialize `result = x`, negate the local `x`, then cover only `x < -4` and `-4 <= x < 4` in the **negated** domain. Therefore an original input `x <= -4` reaches neither predicate and returns the raw input. The compute kernel still materializes `exp(-x)` with the fast-exp path before calling the SFPU helper.

## What the host oracle proves

The runnable model deliberately separates three source-visible error classes:

1. **Missing negative branch:** original `x <= -4` returns raw `x`, dropping the `log1p(exp(x))` correction.
2. **Positive one-term tail:** original `x > 4` returns `-exp(-x)` rather than `-log1p(exp(-x))`; the regression at `4.0001` requires the resulting relative error to exceed 0.8%.
3. **Mid-range polynomial:** at `x=0`, the checked-in constant term gives `-0.692435443...` instead of `-ln(2)`; the regression requires >7e-4 absolute error.

The proposed numerical acceptance identity is source-independent and overflow-safe:

`logsigmoid(x) = min(x, 0) - log1p(exp(-abs(x)))`

The host oracle checks that identity after binary32 input/output rounding throughout `[-30, 30]`. It intentionally uses an accurate host exponential when modeling the current positive tail, so it isolates structural truncation instead of inflating the result with implementation-specific fast-exp error.

## Why current nightly coverage misses the key branch

The pinned nightly sweep still constructs inputs with `torch_random(low=-4, high=10)` and only `bfloat16` / `bfloat8_b`. A continuous random draw cannot exercise values below `-4`, so it cannot cover the missing original-input `x < -4` branch. The host regression has explicit `-4.0001`, `-5`, `-10`, and `-30` controls and explicit `+/-4` continuity checks.

## Commands

```bash
python work/high-value/tt-metal-53787/logsigmoid_oracle.py
python -m pytest -q tests/test_tt_metal_53787_logsigmoid_oracle.py
python -O -m pytest -q tests/test_tt_metal_53787_logsigmoid_oracle.py
python -m py_compile work/high-value/tt-metal-53787/logsigmoid_oracle.py tests/test_tt_metal_53787_logsigmoid_oracle.py
```

The repository-hosted `Unit Tests` workflow runs the full Python test suite on Python 3.9 and 3.13. Any merge receipt must cite the exact PR head's hosted result.

## Upstream acceptance boundary

Before claiming the bounty implementation itself is complete, the assigned owner still needs device/runtime evidence that the real patched Wormhole and Blackhole paths meet the issue's fp32 ULP target, do not regress bf16, cover both tails and the `+/-4` transition, and satisfy required CI. This host artifact is a deterministic regression contract, not a substitute for that hardware evidence.

Prepared/finalized by `ZZ-Sol-Palisade-7341 / GPT-5.6 Sol`; source ownership remains with the upstream assignee.
