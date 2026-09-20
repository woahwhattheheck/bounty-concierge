# tt-metal #52037 — logaddexp / logaddexp2 numerical acceptance oracle

Status: independent source/test artifact only. Upstream bounty is assigned to `EazyHood`; do not open a competing kernel PR or claim from this packet.

Pinned source:
- `tenstorrent/tt-metal main@708e7f58aef9d089f0e5097d4a772c7c8ee129ea`
- issue `#52037`, OPEN and assigned to `EazyHood`
- active assigned carrier `#52856@74999c9a6fdaf75bf6a23be32abd1dead7b35946`
- at observation, carrier vs current main: `ahead 33 / behind 283`

## Host oracle

For finite inputs let `m = max(a,b)` and `d = abs(a-b)`.

Natural base:

```
logaddexp(a,b) = m + log1p(exp(-d))
```

Because `0 < exp(-d) <= 1`, the correction is in `[0, ln(2)]`, so every finite pair obeys

```
m <= result <= m + ln(2)
```

Base 2:

```
logaddexp2(a,b) = m + log2(1 + 2**(-d))
```

and therefore

```
m <= result <= m + 1
```

`vectors.csv` pins deterministic double-precision references plus the correctly-rounded host fp32 projection for threshold, equal, negative, max-dominated and close-large pairs. The close-large rows (`10000,9999.75` and `10000,9999.999`, plus negative mirrors) are deliberate: wide independent random draws mostly make the correction negligible, while these rows force the implementation to preserve the `log1p` correction near the ticket's ±1e4 acceptance edge.

Representative references:

| op | a | b | stable f64 | stable f32 | correction |
|---|---:|---:|---:|---:|---:|
| logaddexp | 89 | 0 | 89 | 89 | ~0 |
| logaddexp | 90 | 89 | 90.3132616875182 | 90.3132629394531 | 0.313261687518221 |
| logaddexp | 100 | 100 | 100.69314718056 | 100.693145751953 | 0.693147180559947 |
| logaddexp | -100 | -100 | -99.3068528194401 | -99.3068542480469 | 0.693147180559947 |
| logaddexp | 10000 | 9999.999 | 10000.6926473056 | 10000.6923828125 | 0.692647305559149 |
| logaddexp2 | 128 | 0 | 128 | 128 | ~0 |
| logaddexp2 | 129 | 128 | 129.584962500721 | 129.5849609375 | 0.584962500721161 |
| logaddexp2 | 127 | 127 | 128 | 128 | 1 |
| logaddexp2 | -150 | -150 | -149 | -149 | 1 |
| logaddexp2 | 10000 | 9999.999 | 10000.9995000866 | 10000.9990234375 | 0.99950008664382 |

No device result is claimed by this oracle.

## Current-main source map

Literal current main still contains the unsafe decomposition in `ttnn/cpp/ttnn/operations/eltwise/binary_ng/device/binary_ng_utils.cpp` (`EXP/EXP/ADD/LOG` and `EXP2/EXP2/ADD/LOG2`). The current `is_binary_sfpu_op` gate routes `LOGADDEXP` / `LOGADDEXP2` to SFPU only for `FLOAT32`; BF16 therefore remains exposed to the composed path on main.

The assigned carrier #52856 materially improves this: its exact head adds fused Wormhole/Blackhole kernels for **both** logaddexp and logaddexp2, widens the BinaryNG SFPU gate to FP32/BF16/BFLOAT8_B/BFLOAT4_B, and adds strong operation-level coverage for threshold values, infinities, scalar/in-place forms, broadcast and block-float routes. The PR body text saying logaddexp2 was left out is stale relative to its current exact head; review should use the code, not that older paragraph.

## Exact-head acceptance gap on #52856

The ticket's success criterion 4 explicitly requires all three composition sites and LOGADDEXP2 variants to be covered. Exact #52856 changes BinaryNG and fused-kernel surfaces, but its changed-file set does **not** include:

`ttnn/cpp/ttnn/operations/eltwise/binary/common/binary_op_utils.cpp`

At current main that file still contains two unsafe composition blocks:

- FPU-style defines: `EXP` on both inputs → `add_tiles` → `LOG` for LOGADDEXP;
- SFPU-style defines: `EXP`/`EXP2` on both inputs → `add_binary_tile` → `LOG`/`LOG2` for LOGADDEXP/LOGADDEXP2.

Therefore final acceptance needs one of two explicit proofs before calling the ticket source-complete:

1. **Reachability proof:** demonstrate those legacy definitions are no longer reachable for every registered public logaddexp/logaddexp2 tensor-tensor, scalar, in-place, dtype and architecture path in scope, and then remove/deprecate the dead composition so it cannot silently return later; or
2. **Repair:** route those still-live sites to an overflow-safe implementation and add a regression that executes the actual path.

This is a source-completeness hold, not a claim that the fused kernels are numerically wrong. Their current exact-head test patch is substantially stronger than the old coverage.

## Regression matrix

A final carrier should prove, at minimum:

- threshold crossing: natural base around 88.7/89 and base-2 around 127/128;
- equal large operands on both signs, where the full `ln(2)` / `+1` correction is required;
- close large operands near ±1e4 (`d=0.001`, `0.25`, `1`) so the correction term cannot disappear under a max-only implementation;
- max-dominated large separation where the result rounds to `max(a,b)`;
- FP32 and BF16 through the public registered op, not only LLK;
- scalar, in-place, broadcast and block-float routing where supported;
- `(+inf,+inf)`, `(-inf,-inf)`, mixed infinities and NaN policy separately from the finite-input theorem;
- explicit path coverage or dead-code proof for every composition site named by the ticket;
- fresh rebase/rejoin to current main before final review, because exact #52856 was 283 commits behind at this observation.

The finite-input invariant is the cheap hostile oracle: for every finite pair, any non-finite output is automatically wrong, and any finite result outside the relevant `[m,m+ln2]` / `[m,m+1]` interval is automatically wrong even before a tolerance comparison to Torch.

No hardware execution, upstream review/comment, bounty claim, assignment change, sponsor contact or payment action was performed by this packet.