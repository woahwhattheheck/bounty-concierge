# tt-metal #55105 — high-order polygamma underflow oracle

Status: independent host-numerics / acceptance artifact only. The upstream bounty is assigned to `singhharsh1708`; do not open a competing kernel PR or claim from this packet.

Pinned source:
- `tenstorrent/tt-metal main@708e7f58aef9d089f0e5097d4a772c7c8ee129ea`
- issue `#55105`, OPEN and assigned to `singhharsh1708`
- assigned carrier `#56332@34122c0455f43c2d706b939f59394cc8808dc331`
- at observation, carrier vs current main: `ahead 7 / behind 116`

Current main Blackhole kernel blob: `62c70bde2f017addf8b48cd61d2a36f827be79c7`.
Current main Wormhole B0 kernel blob: `642c1f289813c112ad6780c3fd58674fa64dfbf5`.

## Source contract

Both current-main kernels accumulate the unscaled Hurwitz-series approximation first and apply

```
scale = (-1)^(n+1) * n!
```

only after the exact six-term sum and Euler-Maclaurin tail have been accumulated. Therefore the accumulator magnitude is approximately `abs(polygamma(n,x)) / n!`; at high order it can cross the fp32 normal floor long before the final mathematical result does. Tensix flush-to-zero behavior makes that separation observable.

The assigned carrier #56332 folds the scale into the exact-term seed and tail coefficients on both Blackhole and Wormhole B0, and also restructures the power chain so a top intermediate power is not formed alone when it can underflow before multiplication into a still-normal scaled value.

## High-precision boundary solution

Using 80-digit `mpmath.polygamma` and the fp32 normal floor `2^-126 = 1.1754943508222875e-38`, two continuous boundaries can be solved independently for every order:

1. **raw-sum boundary**: `abs(polygamma(n,x))/n! = 2^-126`; beyond this, the current unscaled accumulator is subnormal even though the final result may still be normal;
2. **true-result boundary**: `abs(polygamma(n,x)) = 2^-126`; beyond this, the mathematical answer itself is no longer a normal fp32, so zero under an FTZ contract is no longer evidence of the scale-ordering bug by itself.

Exact high-precision boundaries for the bounty orders:

| n | n! | raw-sum normal through x≈ | true result normal through x≈ | recovered x-range |
|---:|---:|---:|---:|---:|
| 7 | 5,040 | 198523.8661770198 | 671012.4871272640 | 3.38000916489× |
| 8 | 40,320 | 42495.33851797968 | 159965.9708818201 | 3.76431807489× |
| 9 | 362,880 | 12835.41487671674 | 53229.02623827865 | 4.14704368729× |
| 10 | 3,628,800 | 4931.987506137424 | 22333.86934451242 | 4.52837103028× |
| 11 | 39,916,800 | 2257.282282402589 | 11079.58414038255 | 4.90837332431× |

These exact boundaries sharpen the issue's asymptotic estimates. In particular, the first integer immediately after each raw-sum boundary is a deterministic hostile where the **current accumulator is already below the fp32 normal floor while the true result is still normal**:

- n=7: x=198524
- n=8: x=42496
- n=9: x=12836
- n=10: x=4932
- n=11: x=2258

Conversely, the first integer after the true-result boundary is where a normal-result assertion must stop:

- n=7: x=671013
- n=8: x=159966
- n=9: x=53230
- n=10: x=22334
- n=11: x=11080

`boundary_vectors.csv` pins both sides of both boundaries with the high-precision result and raw unscaled-sum magnitude.

## Representative vectors

| n | x | true ψ^(n)(x) | raw | true normal? | raw normal? |
|---:|---:|---:|---:|:---:|:---:|
| 7 | 198523 | 5.924672475286861e-35 | 1.1755302530331073e-38 | yes | yes |
| 7 | 198524 | 5.924463572660148e-35 | 1.1754888040992358e-38 | yes | **no** |
| 8 | 42496 | -4.739003045221649e-34 | 1.175347977485528e-38 | yes | **no** |
| 9 | 12836 | 4.263884128173862e-33 | 1.175012160541739e-38 | yes | **no** |
| 10 | 4932 | -4.265525832466783e-32 | 1.175464570234453e-38 | yes | **no** |
| 11 | 2258 | 4.675813874898264e-31 | 1.171389959841035e-38 | yes | **no** |
| 11 | 11079 | 1.176176318820767e-38 | 2.946569661948770e-46 | yes | no |
| 11 | 11080 | 1.175009108901678e-38 | 2.943645555008612e-46 | **no** | no |

The sign alternates with order as expected; the underflow classification is about magnitude.

## Exact-head carrier review

#56332 already fixes both architecture kernels and adds an LLK polygamma sweep for orders 1–11 in Float16_b / Float32. Its test deliberately sets `atol=0`, which is correct: a non-zero mathematical result must not pass merely because an absolute tolerance larger than the result masks a returned zero.

Two acceptance notes remain useful:

1. The new wide-range sweep is marked `nightly`; the carrier body itself notes that no PR lane runs it. Before final acceptance, consume an actual nightly / targeted exact-head run for both architectures or add a small deterministic PR-lane canary using the integer boundary vectors above. Source presence alone is not execution proof.
2. The carrier is 116 commits behind current main at this observation. Rejoin/rebase before final review and re-check the exact two kernel blobs plus the LLK parameter/golden wiring; do not treat old ttsim/hardware receipts as proof for a rebased head without readback.

## Recommended deterministic regression fence

For each n=7..11 and each architecture/dtype path in scope:

- `x=floor(raw_boundary)` and `ceil(raw_boundary)`: both expected outputs are non-zero because the true result remains normal; this pair catches the exact pre-scale FTZ transition without relying on random sampling;
- `x=floor(true_boundary)`: require non-zero and compare to a high-precision host golden;
- `x=ceil(true_boundary)`: do **not** require non-zero under FTZ; compare only according to the platform's documented subnormal policy;
- retain the issue's earlier graded-error points (`n=11,x=1448`, `n=10,x=2816`, `n=9,x=6208`, `n=8,x=17408`, `n=7,x=55808`) so the fix cannot merely postpone hard zero while leaving the pre-zero error band uncontrolled;
- use `atol=0` around the normal/subnormal boundary, plus a relative/ULP criterion appropriate to output dtype;
- keep Wormhole B0 and Blackhole results separate: the current kernels have different power-chain structure even though the scale-ordering defect is shared.

This packet makes no device result, performance claim, upstream review/comment, bounty claim, assignment change, sponsor contact or payment action.