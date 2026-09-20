# Tenstorrent tt-metal #54016 — source-pinned numerical acceptance packet

Status: independent host/reference artifact for the assigned implementation lane. This packet does **not** claim issue assignment, device execution, performance validation, bounty ownership, merge authority, or payout.

## Exact source fence

Captured 2026-09-19/20 UTC from `tenstorrent/tt-metal`:

- Issue `#54016`: **[Bounty $35000] Welford Two-Pass Statistics Optimisation**; open, bounty-labelled, assigned to `jasondavies`.
- Literal upstream `main`: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`.
- Current baseline `ttnn/cpp/ttnn/operations/reduction/generic/device/welford_reduce_program_factory.cpp` blob: `5b9d78366b9d3feb364dddd732f5a1b799bb7009`.
- Current baseline `ttnn/cpp/ttnn/operations/normalization/groupnorm/device/groupnorm_device_operation.cpp` blob: `49fcd611208473c12ef38e6ddabd05d5ac348b2a`.
- Current baseline `ttnn/cpp/ttnn/operations/normalization/layernorm/device/layernorm_types.hpp` blob: `9fa987c02d66a784b5cfe3801ba59e545a622c7d`.
- Current baseline `tech_reports/op_kernel_dev/accuracy_tips/accuracy_tips.md` blob: `26a7387837dc6890daf44f3ff015e93de89a1a0c`.
- Current baseline generic reduction corner test blob: `tests/ttnn/nightly/unit_tests/operations/reduction/test_generic_ops.py@20aa8ef47257f9e84a0a6d469fb4bc0b4f3a6439`.

The assigned carrier is upstream PR `#54786`, open at exact head `b7ccb4a97ad2eff308aed9db725dda93fa65770d`, merge base `38afb36a2b622b24544905176f86132ef14812f1`. At capture time it was mergeable but diverged from literal main by **223 commits ahead / 48 behind**. Therefore its historical device/benchmark evidence is not a substitute for a final rejoin + exact-head validation.

## Baseline facts that constrain acceptance

1. The current standalone Welford reduction path treats FP32 input specially: FP32 input requires `fp32_dest_acc_en=true` so unpack-to-DEST avoids silent TF32 truncation. Any two-pass selector must preserve that precision contract or explicitly route to a backend that does.
2. Current generic reduction tests exercise `var`/`std` population and sample semantics through `correction=False/True` and compare against PyTorch.
3. Current GroupNorm accepts BF16 or FP32 input. The Welford interleaved path requires TILE input/output; sharded validation has independent grid/shard constraints. A selector must not accidentally route ROW_MAJOR interleaved input into the Welford/two-pass-only assumptions.
4. LayerNorm's public program configs contain `use_welford` for both default and sharded configurations. PR #54786 intentionally preserves that external knob while changing the selected statistics backend, so compatibility must be tested as selector behavior rather than by renaming the API.
5. The current accuracy guide describes Welford as the large-offset accuracy path. Replacing it is an accuracy-sensitive semantic change even when a benchmark improves.

## Host numerical oracle

`variance_oracle.py` deliberately separates three things:

- an 80-digit Decimal reference for population/sample variance;
- a deterministic host model of the proposed shifted two-pass formulation with explicit FP32/BF16 input quantization and FP32 arithmetic rounding;
- an intentionally unstable `E[x²] - E[x]²` comparator to prove that a hostile vector is actually cancellation-sensitive.

The host model is **not** an SFPU/device simulator. Its job is to make the mathematical acceptance vectors reproducible and to prevent weak tests from silently becoming easy because the reference suffers the same cancellation.

Run locally from this directory:

```bash
python test_variance_oracle.py
python -O test_variance_oracle.py
python variance_oracle.py
```

The source-publish run passed **11/11 normal + 11/11 optimized** tests. The adversarial examples include positive and negative large common offsets, representable low spread, a `1e8` ULP-scale case, constants, zero crossing, alternating values, both correction modes, BF16-input/FP32-accumulator smoke, and a same-multiset shift-anchor ordering hostile.

Representative host result: for 32 FP32-representable values spaced by `0.125` around `1,000,000`, the Decimal population variance and shifted-FP32 host model are both `1.33203125`, while the hostile raw-square comparator returns `65536.0`. For a negative-offset vector around `-65536`, the shifted result remains `0.02081298828125` while the raw-square comparator becomes `-512.0`. These are host arithmetic facts only, not device measurements.

## Review-ready validation matrix

### Standalone `var` / `std`

Cross the following dimensions before accepting a selector change:

| Axis | Required cases | Acceptance property |
| --- | --- | --- |
| op | `var`, `std` | `std` remains consistent with the accepted variance semantics and output quantization. |
| correction | `0`, `1` | Denominator is `N-correction`; invalid `N-correction <= 0` follows existing API behavior. |
| reduction | W, H, HW, folded/batch reduction | No partial-state/final-scalar confusion across combine stages. |
| dtype | FP32; BF16 where enabled; BFP8 only where the carrier explicitly supports it | Compare against a high-precision reference **after input quantization**. |
| magnitude | near-zero, `+large`, `-large` | Translation stability is symmetric in sign. |
| variance | ordinary, low, near-zero, exact-zero | Finite input must not produce a negative variance or NaN from cancellation. |
| shape | one tile, partial H/W tail, long reduction, uneven combine tree | Padding never contributes and replay/streaming boundaries agree. |
| ordering | representative first sample, extreme/outlier first, reversed same multiset | Choice of shift anchor must not turn input ordering into a material accuracy dependency. |
| scalar | unity, zero, positive/negative non-unity where API supports it | Preserve existing post-reduction scaling semantics and rounding order. |

For FP32, a final carrier should retain explicit coverage equivalent to the current unpack-to-DEST precision fence. A numerically stable formula cannot recover information already truncated to TF32.

### GroupNorm

Validate separately rather than assuming standalone reduction coverage transfers:

- BF16 and FP32 input where supported.
- Interleaved TILE path and sharded path; ROW_MAJOR interleaved remains a legacy/control route unless the new carrier explicitly changes that contract.
- Rectangular sharding validation, group/channel divisibility, partial logical-vs-padded H*W, input-mask behavior, affine/no-affine, and program-cache reuse.
- Large positive/negative common offsets, tiny variance, exact constants, and multiple batches/groups.
- Selector evidence must say which backend actually ran; numerical success alone does not prove the intended two-pass path was exercised.
- PR #54786 removes the GroupNorm reciprocal-table API from its changed surface. Acceptance therefore includes a repository-wide caller migration check; stale callers are an integration failure even if the kernels are correct.

### LayerNorm

The assigned carrier already has an explicit statistics selector. Acceptance should cross:

- interleaved vs sharded;
- Wormhole vs Blackhole; Quasar/control behavior;
- FP32 destination accumulation enabled/disabled;
- `use_welford` requested vs not requested;
- BF16/FP32/BFP8 only where supported by the selected path;
- plain, gamma-only, beta-only, full affine, residual/pre-add, and widths immediately around every calibrated selector boundary;
- compact L1 replay fits vs does not fit;
- large-offset low-variance rows with both representative and unrepresentative first-value anchors;
- program-cache reuse after L1 occupancy changes.

Distributed LayerNorm should remain an explicit control unless a final carrier intentionally changes it; PR #54786's description says distributed LayerNorm remains online Welford.

## Performance acceptance without inventing measurements

No performance number is accepted merely because it appears in an old PR description. For each architecture/workload used to justify routing:

1. rejoin the final candidate onto the intended current base and record both exact SHAs;
2. build baseline and candidate with the same toolchain/configuration, firmware, KMD, device, clock/power policy, and environment;
3. prove the selector/backend chosen for the measured input;
4. use the same warmup and measurement harness, randomized or alternated revision order when practical, and report run counts plus a distribution (at minimum median and spread), not one sample;
5. run numerical validation on the same revision used for timing;
6. keep slow/regressed shapes on the existing backend when that is the selector contract;
7. label historical measurements as historical when their source revision differs from the current candidate.

## Current-carrier integration watch

PR #54786 is a large multi-surface change and, at capture time, was 48 commits behind current main. Before final acceptance, re-run a changed-path and semantic-drift review after rejoin. In particular, recheck current-main evolution in reduction program factories, GroupNorm validation/masking, LayerNorm selector inputs, L1 allocator accounting, and any callers of the removed GroupNorm reciprocal helper. This packet intentionally does not declare the assigned carrier green or red without that exact-head rejoin and hardware/provider evidence.
