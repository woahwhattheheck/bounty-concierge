# tt-metal #56290 — exact-output clamp oracle and path map

Status: independent host/reference artifact. Implementation ownership remains with assigned contributor `singhharsh1708`; no competing upstream kernel PR, issue-assignment mutation, hardware claim, bounty claim, or payout action is made here.

## Source fence

Captured from `tenstorrent/tt-metal` on 2026-09-20 UTC:

- Issue `#56290`: open, bounty-labelled, assigned to `singhharsh1708`.
- Literal upstream `main@708e7f58aef9d089f0e5097d4a772c7c8ee129ea`.
- `ttnn/cpp/ttnn/operations/eltwise/quantization/quantization.cpp` blob `b31f5928e3986ebcf593156c1713a56274965469`.
- `tests/ttnn/nightly/unit_tests/operations/eltwise/test_quantization.py` blob `95805e96492acaa405b8204a57e72fef6af29b66`.
- Assigned carrier: upstream PR `#56327`, open/mergeable at exact head `0c4090ec15ba53369321815870e02069f7ba68d2` from `singhharsh1708/tt-metal`; at capture it was **10 commits ahead / 3 behind** literal current main, merge base `54a826b1d931cf5c70f1d933194cfc32ea96b2b6`.

The issue contract is exact:

```text
quantize:   clamp(round(input / scale + zero_point), 0, 255)
requantize: clamp(round((input - input_zero_point) * input_scale / output_scale + output_zero_point), 0, 255)
```

The checked-in tests already use `torch.clamp(torch.round(...), 0, 255)` for upper saturation. The assigned carrier extends that exact-output contract to the missing lower-saturation cases.

## Direct vs composite path map

Current `quantization.cpp` has materially different routes, so device validation must identify which route a case exercises.

| API shape | Current route | #56290 relevance |
| --- | --- | --- |
| `quantize`, scalar float scale + scalar int zero-point | fused `binary_ng(... QUANT ...)` | Direct SFPU path; the issue reports negative UInt8 magnitude reflection here. |
| `quantize`, tensor zero-point (or tensor/tensor fallback) | divide/add composite → `narrow_composite_result` | Current main narrows UINT8 with `ttnn::typecast` because only INT8 is redirected through `quantize`; that can wrap/truncate out-of-range values. Assigned #56327 redirects UINT8 through the saturating quant path too. |
| `requantize`, all four parameters scalar | fused `binary_ng(... REQUANT ...)` | Direct SFPU path; lower clamp must happen before UInt8 conversion while preserving INT8’s excess-128 handling. |
| `requantize`, mixed scalar/tensor parameters | `dequantize(...)` → `quantize(...)` | Composite path inherits the quantize output contract; tensor argument combinations must still be represented in coverage. |
| `requantize`, `axis` + all tensor params | expanded arithmetic → typecast | Narrow INT8/UINT8 output is rejected before this path on current main, so do not mislabel this as uint8 coverage. |
| per-channel quantize/requantize with narrow output | rejected on current main | Out of scope for the bounty unless the public API contract is intentionally expanded. |

## Exact-output host oracle

`clamp_oracle.py` uses Decimal and explicit `ROUND_HALF_EVEN`, then saturates to the destination bounds. That gives deterministic expected integers without depending on Python binary-float accident. It deliberately models the **public math contract only**; it is not an SFPU simulator.

The table contains 30 named cases: 16 quantize and 14 requantize. It covers:

- negative values well below zero and immediately below the lower boundary;
- tie-to-even points (`0.5`, `1.5`, and requantized half steps);
- zero and ordinary in-range values;
- non-zero input/output zero points and unequal scales;
- the last in-range UInt8 region and upper saturation to 255;
- INT8 lower/in-range/upper controls so a UInt8 repair cannot silently change signed behavior;
- an explicit UINT8-input → UINT8-output requantize hostile (`q_in=1`, `input_zero_point=10`) which must clamp to `0`;
- the issue’s critical anti-symmetry hostile: `quantize(-5, 0.1, 0, uint8) == 0`, while `quantize(+5, 0.1, 0, uint8) == 50`.

Run from this directory:

```bash
python test_clamp_oracle.py
python -O test_clamp_oracle.py
python clamp_oracle.py
```

## Device acceptance matrix for the assigned carrier

Host exactness is necessary but not sufficient. Authorized Wormhole B0 + Blackhole validation should cross:

1. architecture: WH B0 / BH;
2. operation: quantize / requantize;
3. route: direct scalar kernel / composite tensor-argument fallback;
4. source dtype: FP32 + BF16 for quantize; INT32 + INT8 + UINT8 routes for requantize;
5. output dtype: UINT8 target plus INT8 control;
6. boundary class: negative, `-0`, zero, tie, ordinary in-range, 254/255 edge, >255, very large magnitude;
7. zero-point: zero and non-zero; scales equal and unequal for requantize;
8. exact-output assertion, not PCC/match-ratio only.

For every candidate device test, compute expected output with the host oracle **after applying whatever input dtype quantization the test fixture actually sends**. Record the exact git head, architecture, test selector, and route. Historical device evidence on #56327 is useful but should not replace a final current-main rejoin because the carrier was three commits behind at capture.

## Review fence

A complete fix is not just “negative becomes zero” in one SFPU branch. Acceptance requires all of the following together:

- direct UInt8 QUANT lower saturation;
- direct UInt8 REQUANT lower saturation, including UINT8 input below `input_zero_point` → UINT8 output `0`;
- upper saturation remains 255;
- composite UInt8 narrowing no longer wraps/truncates out-of-range results;
- INT8 exact outputs are unchanged on boundary vectors;
- replay/init body lengths and selected tile op agree on WH/BH;
- final carrier is rejoined to current main and the same exact head is used for functional/device evidence.
