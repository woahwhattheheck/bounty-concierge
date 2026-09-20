# Tenstorrent tt-metal #52037 — overflow-safe logaddexp acceptance oracle

Status: independent host/reference artifact for the **assigned** implementation lane. This packet does **not** claim issue assignment, Tenstorrent hardware execution, performance validation, implementation ownership, bounty entitlement, merge acceptance, or payout.

## Exact source fence

Captured 2026-09-19/20 UTC from `tenstorrent/tt-metal`:

- Issue [#52037](https://github.com/tenstorrent/tt-metal/issues/52037): **[Bounty $1500] logaddexp / logaddexp2: overflow-safe reformulation**.
- Issue state: OPEN, `bounty` + `bounty_difficulty/medium`, assigned to `EazyHood`.
- Literal upstream `main`: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`.
- `ttnn/cpp/ttnn/operations/eltwise/binary_ng/device/binary_ng_utils.cpp` blob: `c373108e47be43abacbb2edd458f15f475850b8c`.
  - current `LOGADDEXP` at lines 251–255 is `EXP(lhs)` + `EXP(rhs)` → ADD → LOG;
  - current `LOGADDEXP2` at lines 258–262 is `EXP2(lhs)` + `EXP2(rhs)` → ADD → LOG2.
- `ttnn/cpp/ttnn/operations/eltwise/binary/common/binary_op_utils.cpp` blob: `9a9ca5eae81a730a2561dbac92489ceb0fd18c50`.
  - FPU composition begins at lines 130 / 170;
  - SFPU composition begins at lines 401 / 410;
  - both retain the same pre-exp / add / post-log shape.

The acceptance artifact therefore targets the **current source contract**, not a speculative kernel implementation.

## Why the current composition fails

For finite inputs, the mathematical results are bounded:

```text
max(a,b) <= logaddexp(a,b)  <= max(a,b) + ln(2)
max(a,b) <= logaddexp2(a,b) <= max(a,b) + 1
```

So a finite-input `±inf` result is never required by the result range. It comes from the intermediate exponential.

The runnable oracle models the current composition with binary32 intermediate rounding plus FTZ for subnormal intermediates:

```text
EXP/EXP2(a) -> EXP/EXP2(b) -> binary32 ADD -> LOG/LOG2
```

and compares it with the overflow-safe identities:

```text
logaddexp(a,b)  = m + log1p(exp(-abs(a-b)))
logaddexp2(a,b) = m + log1p(exp(-abs(a-b))*ln-base conversion)
m = max(a,b)
```

The exponent in the correction is never positive, so it cannot overflow. Equal large-negative inputs also remain safe because the correction evaluates at zero rather than exponentiating the original magnitude.

## Required boundary vectors

Representative binary32 acceptance rows:

| op | a | b | current-style naive/FTZ | stable binary32 |
|---|---:|---:|---:|---:|
| logaddexp | 100 | 0 | +inf | 100 |
| logaddexp | 89 | 0 | +inf | 89 |
| logaddexp | 90 | 89 | +inf | 90.31326293945312 |
| logaddexp | 100 | 100 | +inf | 100.69314575195312 |
| logaddexp | 200 | 199 | +inf | 200.31326293945312 |
| logaddexp | -100 | -100 | -inf (FTZ) | -99.30685424804688 |
| logaddexp | 88 | 88 | 88.69314575195312 | 88.69314575195312 |
| logaddexp | 5 | 3 | 5.126927852630615 | 5.126927852630615 |
| logaddexp | 88.7 | 0 | 88.69999694824219 | 88.69999694824219 |
| logaddexp | 1000 | 999 | +inf | 1000.313232421875 |
| logaddexp | 10000 | 10000 | +inf | 10000.693359375 |
| logaddexp | -10000 | -10000 | -inf (FTZ) | -9999.306640625 |
| logaddexp2 | 126 | 126 | 127 | 127 |
| logaddexp2 | 128 | 128 | +inf | 129 |
| logaddexp2 | -126 | -126 | -125 | -125 |
| logaddexp2 | -128 | -128 | -inf (FTZ) | -127 |
| logaddexp2 | 10000 | 10000 | +inf | 10001 |
| logaddexp2 | -10000 | -10000 | -inf (FTZ) | -9999 |

The FTZ switch is explicit in the model. Turning FTZ off makes the `(-100,-100)` current-style logaddexp intermediate subnormal rather than zero, which distinguishes the generic IEEE-754 underflow model from the failure class reported by the issue.

## Regression contract

The companion tests require all of the following:

1. The issue's positive overflow rows become finite under the stable formulation.
2. The issue's negative FTZ row is reproduced by the naive model and becomes finite under the stable formulation.
3. `logaddexp2` covers the reported `126/128` and `-126/-128` transitions.
4. Stable host results are finite for a cross-product grid through `|a|,|b| = 1e4`.
5. The exact mathematical finite-input bounds hold on the double reference.
6. Binary32 stable results stay within a binary32-rounding tolerance of that reference.
7. Operand symmetry is exact after input/output binary32 rounding.
8. Existing moderate-range values remain close to the current naive binary32 composition.
9. Non-finite inputs are rejected by this acceptance oracle instead of being silently folded into the finite-input contract.
10. JSON report output preserves `inf` / `-inf` explicitly rather than serializing non-finite evidence ambiguously.

## Commands

```bash
python work/high-value/tt-metal-52037/logaddexp_oracle.py
python -m pytest -q tests/test_tt_metal_52037_logaddexp_oracle.py
python -O -m pytest -q tests/test_tt_metal_52037_logaddexp_oracle.py
python -m py_compile work/high-value/tt-metal-52037/logaddexp_oracle.py tests/test_tt_metal_52037_logaddexp_oracle.py
```

The repository's hosted `Unit Tests` workflow runs `pytest -v tests` on Python 3.9 and 3.13. A merge receipt must cite the **exact PR head's** hosted result; this document itself does not predeclare CI green.

## Upstream use

The assigned implementation owner can use this as a source/test contract while choosing the actual TTNN implementation shape. The artifact deliberately does not choose between a dedicated fused binary operation and a higher-level restructured composition.

Before an upstream implementation is accepted, re-pin current main and prove:

- all three current composition sites are removed or bypassed for both variants;
- the device path passes equivalent boundary vectors in FP32 and BF16;
- the currently working range remains within the op's established tolerance;
- architecture-specific device behavior is validated on the architectures required by Tenstorrent.

Host arithmetic is acceptance support, **not** device evidence.
