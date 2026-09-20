# tt-metal #56277 — legacy SFPU reachability inventory + removal acceptance

Status: source-pinned independent acceptance artifact for the assigned removal lane. **No competing upstream removal PR, issue-assignment mutation, device/performance claim, bounty claim, or payout action is made here.**

## Exact source fence

Captured 2026-09-20 UTC:

- Issue: `tenstorrent/tt-metal#56277`, open/bounty-labelled, assigned to `jasondavies`.
- Literal upstream main: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`.
- Current `tt_metal/hw/inc/api/compute/eltwise_unary/rsqrt.h`: blob `fcac56c37a2da9c9c2dd92a4c518959b38c0cbd5`.
- Current `tt_metal/hw/inc/api/compute/eltwise_unary/recip.h`: blob `9b0057d15e712127bc5fd3f097ffb2dec71ff3d9`.
- Current `ttnn/cpp/ttnn/operations/normalization/layernorm/device/layernorm_types.hpp`: blob `9fa987c02d66a784b5cfe3801ba59e545a622c7d`.
- GitHub code-search census on that main revision reports **47 files containing `legacy_rsqrt`**, **29 files containing `legacy_compat`**, and **8 files containing `ckernel_sfpu_rsqrt_compat`**. These are file counts, not occurrence counts, and are a baseline reachability signal rather than proof that every hit is executable.
- Assigned carrier: upstream PR **#56292**, `jasondavies/drop-legacy-sfpu-math`, exact observed head `87c0379134823740bdee59dab84c0efc554fefe3`, merge base `57bf536b748eaa2354df449e072c640ef0b1c242`. At capture it was **29 commits ahead / 43 behind** literal main, so its historical test/performance evidence is not final-current-head proof.

The current public APIs still expose Boolean compatibility templates:

- `rsqrt_tile_init<legacy_compat>()` and `rsqrt_tile<legacy_compat,...>()`;
- `recip_tile_init<legacy_compat,...>()` and `recip_tile<legacy_compat,...>()`.

PR #56292 replaces those ambiguous Boolean templates with strongly typed controls: `RsqrtMode`, `ReciprocalDestAcc`, and `ReciprocalApproxMode`, and makes `rsqrt_tile_init()` non-templated. This matters because removed Boolean arguments must not silently reinterpret as a precision/fast-mode selector.

## Removal-impact inventory

The assigned carrier's current-main comparison touches the following reachability classes. These are the classes that must stay in the final no-legacy audit even if individual filenames move during rejoin.

| Class | Current-main / carrier evidence | Removal risk / acceptance |
| --- | --- | --- |
| **Core LLK/SFPU** | WH/BH/Quasar `ckernel_sfpu_{sqrt,rsqrt,recip}.h`; WH/BH `llk_sfpu_types.h`; two `tt-llk/*/common/inc/sfpu/ckernel_sfpu_rsqrt_compat.h` copies | Compatibility implementations/headers gone; no `legacy_compat` template plumbing; new mode types preserve explicit precision/approx behavior. |
| **Compute API** | `tt_metal/hw/inc/api/compute/eltwise_unary/{rsqrt,recip}.h` | Removed Boolean template calls must fail to compile rather than bind a different semantic argument. Reciprocal init/call overrides must match. |
| **LayerNorm / RMSNorm** | LayerNorm types, program factories and standard/sharded/large/welford kernels; distributed LN; distributed RMSNorm; experimental fused RMS paths | `legacy_rsqrt` must not select math anywhere. Preserve `legacy_reduction` and `use_welford` as independent controls; do not accidentally remove them with the old rsqrt flag. |
| **GroupNorm** | standard/sharded/Welford kernels plus mcast/no-mcast factories and program utils | Revalidate BF16/FP32, explicit vs auto block choices, masks, sharding and L1 accounting. #56292 includes additional GroupNorm numerical work, so distinguish legacy-removal proof from those coupled fixes. |
| **Reductions / training** | Frobenius normalize, polynorm fw/bw; Layer/RMS reductions | Verify sqrt/rsqrt/recip edge behavior in both precise/approx modes and DEST precision classes. |
| **Attention / SDPA** | standard SDPA, experimental Quasar SDPA/decode, WH/BH experimental SFPU SDPA, tt-train SDPA, attention softmax | Reciprocal precision overrides are sensitive: prove intended `ReciprocalApproxMode`/DEST at each changed caller and match init/call. |
| **Sampling** | DeepSeek v3 sampling host/micro-op and BH experimental SFPU sampling + LLK sampling tests | Ensure the removal does not alter selection/normalization beyond the explicit new API. |
| **Model configs** | BGE Large EN, sentence-BERT WH/BH, Falcon7B, BGE-M3, SDXL base/refiner configs and transformer blocks | Every former `legacy_rsqrt=True/False` model config must either disappear or become an intentional deprecated Python-call boundary; no silent behavior switch. |
| **Graph/config reconstruction** | Llama32 Quasar graph cases, GPT-OSS/Qwen3-VL graph cases, sweep master config loader, traced RMSNorm config | Serialized/reconstructed configs must round-trip without the removed field and reject stale Boolean payloads where the public contract requires rejection. |
| **Kernel-lib chained unary ops** | `kernel_lib/eltwise/{api/chain.hpp,core/op_params.hpp,unary/math.*}` + chain tests | Enum/template removal must propagate through generated/chained op parameters; no hidden default legacy route. |
| **Test infrastructure** | tt-llk golden generators, params/domains/variant helpers, SFPU unary/SDPA/sampling tests; TTNN normalization/fused tests | Delete legacy-only variants while retaining edge coverage for the modern route. A reduced test count is not proof unless the semantic matrix remains. |

### Explicit non-target controls

`legacy_reduction` and `use_welford` are **not** #56277 legacy sqrt/rsqrt/reciprocal compatibility plumbing. The assigned PR states they remain independent choices. `legacy_reachability_audit.py` intentionally does not flag those names.

## No-legacy-reachability checklist

Run `legacy_reachability_audit.py <tt-metal-checkout> --json` on current main to establish the baseline, then on the exact candidate head. Candidate acceptance requires:

1. zero blocking hits for `legacy_compat`, the compatibility header, and Boolean `rsqrt`/`recip` template calls;
2. zero model/config/kernel `legacy_rsqrt` hits;
3. if the provider intentionally retains PR #56292's **deprecated ignored Python keyword**, rerun with `--allow-deprecation-shim`; only `ttnn/cpp/ttnn/operations/normalization/layernorm/layernorm_nanobind.cpp` may be allowlisted, and every other `legacy_rsqrt` hit still blocks;
4. physical deletion of both WH/BH `ckernel_sfpu_rsqrt_compat.h` files;
5. no stale enum/template/config serialization field in graph reconstruction or sweep/model configs;
6. no removed-Boolean call that compiles by accidental conversion to `RsqrtMode`/`ReciprocalDestAcc`; compile-negative tests should cover this API fence;
7. reciprocal `recip_tile_init` and `recip_tile` use matching destination/approximation overrides at each explicit override caller;
8. final scan and functional/device tests are run on the **same exact rejoined head**.

The deprecated-keyword exception is a provider/API compatibility decision, not a claim that the strict issue wording has been satisfied. If strict acceptance requires *no* remaining configuration plumbing, run the scanner without the allowlist and treat that shim as blocking.

## Numerical regression matrix

Use current **non-legacy** behavior on the same base as the control. For precise/reference-comparable cases, also compare against IEEE/PyTorch semantics after source/DEST quantization. For approximate modes, require bounded error plus non-legacy control equivalence rather than pretending bit-exactness.

| Operation | Mode / DEST | Inputs that must be explicit | Semantic fence |
| --- | --- | --- | --- |
| sqrt | approximate + precise; BF16 + FP32 DEST where supported | `+0`, `-0`, positive subnormal/small, ordinary positive, large, `+inf`, finite negative, `-inf`, `NaN` | Preserve sign/NaN/inf handling and current modern approximation selection. Legacy removal must not change unrelated approximation globally. |
| rsqrt | `RsqrtMode::Default`; fast mode where supported; BF16 + FP32 DEST | same edge set, plus values around epsilon used by normalization | Default mode must preserve negative-input NaN behavior documented by #56292. Fast negative-input behavior is explicitly unspecified by the carrier, so test nonnegative fast accuracy and ensure callers do not rely on negative fast results. |
| reciprocal | default/precise/approx `ReciprocalApproxMode`; BF16/FP32 `ReciprocalDestAcc` | `+0`, `-0`, small ±, ordinary ±, large ±, `+inf`, `-inf`, `NaN` | Preserve signed reciprocal edge behavior and explicit precision choice; init/call mode mismatch is a failure. |

For every cell, record architecture (WH B0/BH; Quasar only where supported), source dtype, DEST accumulation mode, exact caller/backend, exact git head and whether the path is standalone, normalization, SDPA/softmax, sampling, or fused/distributed.

## Integration acceptance by surface

- **LayerNorm/RMSNorm/distributed norm:** standard + sharded + Welford/non-Welford + fused pre-add/affine and model configs that formerly set `legacy_rsqrt=True`.
- **GroupNorm:** WH/BH, BF16/FP32, standard/sharded/Welford, auto and explicit block choices; attribute #56292's additional GroupNorm numerical fixes separately from the removal itself.
- **SDPA/attention:** standard/streaming plus experimental Quasar and WH/BH helper paths; explicit reciprocal precision overrides must be visible in test logs/config.
- **Sampling:** DeepSeek + LLK SFPU sampling exact-source smoke and numerical controls.
- **Graph reconstruction:** stale payload with removed field must not silently reconstruct a different config; clean payload round-trips.
- **Representative models:** BGE Large EN, sentence-BERT, Falcon7B, BGE-M3, SDXL base/refiner. Record which formerly consumed `legacy_rsqrt` rather than saying “models pass” generically.

## Performance evidence fence

No performance claim is made by this artifact. Final regression evidence should compare baseline and rejoined candidate with the same hardware, firmware/KMD, toolchain, environment, clocks/power policy, shapes, warmup and sample count; report distributions rather than a single sample. Because #56292 is 43 commits behind current main at capture and also carries GroupNorm/SDXL/softmax numerical changes, do not attribute a performance delta to “legacy removal” without isolating those coupled changes.
