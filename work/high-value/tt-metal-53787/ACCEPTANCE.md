# Tenstorrent tt-metal #53787 — logsigmoid numerical acceptance packet

Status: independent host/reference artifact for the **assigned** implementation lane. It does **not** claim issue assignment, device execution, performance validation, implementation ownership, bounty entitlement, merge acceptance, or payout.

## Exact source fence

Captured 2026-09-19/20 UTC from `tenstorrent/tt-metal`:

- Issue [#53787](https://github.com/tenstorrent/tt-metal/issues/53787): **[Bounty $5k] ttnn.log_sigmoid: 0.91% peak fp32 error and a missing x <= -4 branch**.
- State: OPEN, `bounty` + `bounty_difficulty/hard`, assigned to `kanapitsas`.
- Literal upstream `main`: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`.
- Wormhole `ckernel_sfpu_logsigmoid.h`: `cdc5ad762c24219187ad8e6babe57a2d261474ff`.
- Blackhole `ckernel_sfpu_logsigmoid.h`: `e2ca2e3e875ffcd78f1031df5cfdf4de59b9e0a9`.
- Compute caller `logsigmoid_kernel.cpp`: `5e6522f035fc2856e805997a678dbfa63a7735f4`.
- Main sweep `log_sigmoid.py`: `4653c5028a3df72684b11d87cbcd3b29bd4e9285`.

Both architecture headers still initialize `result = x`, replace local `x` with `-x`, then cover only transformed `x < -4` and `-4 <= x < 4`. In original-input coordinates that leaves **all original x <= -4** uncovered, so those rows return the raw input. The compute caller still computes `Exp<Approx::Fast>(-x)` before the logsigmoid helper. The primary sweep still starts at `low=-4`, so it cannot sample below the missing branch.

## Source model

`logsigmoid_oracle.py` models the checked-in arithmetic with three deliberate fences:

1. the exact current branch partition is preserved;
2. the checked-in degree-8 coefficients are evaluated with non-fused binary32 multiply/add rounding;
3. the positive tail uses an **accurate** host `exp(-x)`, not an invented model of TT `Approx::Fast`.

That third point is conservative: any measured tail error in this host model is caused by the one-term `-exp(-x)` approximation itself. Device fast-exp error is not fabricated.

The acceptance reference is the stable identity:

```text
logsigmoid(x) = min(x, 0) - log1p(exp(-abs(x)))
```

Its exponential argument is never positive.

## Boundary evidence

Representative host-model rows from the pinned source contract:

| x | current source model | stable reference | key defect |
|---:|---:|---:|---|
| -5 | -5 | -5.006715348489118 | raw-input missing branch |
| -4.0001 | -4.000100135803223 | -4.018248262746002 | raw-input missing branch |
| -4 | -4 | -4.018149927917809 | raw-input missing branch |
| -3.9999 | -4.003975868225098 | -4.018051827396636 | polynomial |
| -1 | -1.3061909675598145 | -1.313261687518223 | polynomial |
| 0 | -0.6924354434013367 | -0.6931471805599453 | polynomial |
| 1 | -0.31501784920692444 | -0.3132616875182229 | polynomial |
| 4 | -0.018150150775909424 | -0.01814992791780974 | polynomial |
| 4.0001 | -0.018313804641366005 | -0.01814812694277896 | one-term positive tail |
| 5 | -0.0067379469983279705 | -0.006715348489118068 | one-term positive tail |

The `x=-4` row is more than 38,000 binary32 representable steps from the rounded reference in this source model. The `x=4.0001` row has >0.9% relative error even with accurate host exp, isolating the series truncation from any fast-exp contribution.

## Regression contract

The companion tests require:

1. every original input `x <= -4` in the source model returns raw `x`, making the missing branch explicit;
2. `x=-4` retains >0.018 absolute error and >38k fp32-step error in the current model;
3. checked-in polynomial rows at `-1,0,1,4` match the pinned coefficients exactly under the declared non-fused model;
4. mid-range errors cannot hide behind a loose `atol=0.05` characterization;
5. `x=4.0001` preserves >0.9% one-term-tail error while using accurate exp;
6. the stable identity remains finite and non-positive across a `[-30,30]` boundary grid;
7. the acceptance vectors include both sides of `-4` and `4`;
8. non-finite values are outside this finite-input acceptance contract and fail closed.

## Commands

```bash
python work/high-value/tt-metal-53787/logsigmoid_oracle.py
python -m pytest -q tests/test_tt_metal_53787_logsigmoid_oracle.py
python -O -m pytest -q tests/test_tt_metal_53787_logsigmoid_oracle.py
python -m py_compile work/high-value/tt-metal-53787/logsigmoid_oracle.py tests/test_tt_metal_53787_logsigmoid_oracle.py
```

Hosted `Unit Tests` run `pytest -v tests` on Python 3.9 and 3.13. The merge receipt must cite the exact PR head's hosted result.

## Upstream handoff

This packet is intended to help the assigned implementation owner distinguish three source-level error classes and add acceptance coverage without duplicating the kernel patch. Before an upstream fix is accepted, re-pin current main and require real device CI for the requested fp32 path, both Wormhole and Blackhole source changes, a sweep that samples below `-4`, and error-sensitive tolerances.

Host/source arithmetic is acceptance support, **not** silicon evidence.
