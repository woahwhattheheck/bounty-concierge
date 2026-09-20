# tt-metal #49307 — Command-R host semantic contract

**Lane:** `HVQ-TT-METAL-20260920-49307/COMMAND-R-HOST-CONTRACT+BOUNDARY-FIXTURES`  
**Seat:** `ZZ-Sol-Verdigris-4729 / GPT-5.6 Sol`  
**Economics:** canonical `tenstorrent/tt-metal#49307` is OPEN and advertises **$2,500**, but is assigned to `morhimanshu`. This packet does **not** compete for implementation or payout ownership.

## Purpose

Freeze the small semantic seams that can silently break a Command-R TTNN port when shared Llama-family components look reusable. This is a dependency-free host oracle plus adversarial fixtures. It makes **no T3K, device, PCC, throughput, latency, acceptance, assignment, or payment claim**.

## Source pins

Observed 2026-09-19 / 2026-09-20 UTC boundary:

- Bounty issue: `tenstorrent/tt-metal#49307` — `[Bounty $2500] Command-R bringup using TTNN APIs`; OPEN; assignee `morhimanshu`.
- tt-metal main: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`.
  - `models/tt_transformers/tt/attention.py` blob `3689e9c963b4b1e7da5ec5b744f1054fe8af6627`
  - `models/tt_transformers/tt/mlp.py` blob `4995526341807ff9147fe469b51328457c5b2ed4`
  - `models/tt_transformers/tt/rope.py` blob `da27cf0227603bc3ea5cc8ed04625e22b0fafe02`
  - `models/tt_transformers/tt/lm_head.py` blob `2e3faf89e7cbecce7ac046b711f198bf34f8cf30`
- Hugging Face Transformers main: `c587bc884db2c2e31fc2b8102314656b17aa07b1`.
  - `src/transformers/models/cohere/modeling_cohere.py` blob `c2850690f0b91e7f515ad5dd057f90d1f226713f`.
- Packet carrier base: `woahwhattheheck/bounty-concierge@72e56cdc8a4a3d574eab97c656472e992dbb1e98`.

Pinned source links:

- https://github.com/tenstorrent/tt-metal/issues/49307
- https://github.com/tenstorrent/tt-metal/blob/708e7f58aef9d089f0e5097d4a772c7c8ee129ea/models/tt_transformers/tt/attention.py
- https://github.com/tenstorrent/tt-metal/blob/708e7f58aef9d089f0e5097d4a772c7c8ee129ea/models/tt_transformers/tt/mlp.py
- https://github.com/tenstorrent/tt-metal/blob/708e7f58aef9d089f0e5097d4a772c7c8ee129ea/models/tt_transformers/tt/lm_head.py
- https://github.com/huggingface/transformers/blob/c587bc884db2c2e31fc2b8102314656b17aa07b1/src/transformers/models/cohere/modeling_cohere.py

## Concrete source findings

### 1. Q/K normalization is **not** a drop-in use of the current TT RMSNorm hook

Current `tt_transformers/tt/attention.py` optionally constructs `RMSNorm` for Q and K weights and applies those norms before RoPE. Current Hugging Face Cohere instead defines `CohereLayerNorm` as **mean-centered LayerNorm** over the head dimension, then applies it to Q and K before rotary.

The two operations are not numerically interchangeable. The hostile `[1,2,3,4]` vector in this packet makes the difference explicit: Cohere LayerNorm has approximately zero mean; RMSNorm does not. Any Command-R adapter that merely routes Cohere Q/K norm weights into the existing RMSNorm hook must STOP until the semantic mismatch is repaired or independently proven irrelevant for the exact reference revision.

### 2. Cohere rotary ordering needs an explicit convention test

Current Hugging Face Cohere defines `rotate_half` by pairing even/odd values:

`[x0,x1,x2,x3] -> [-x1,x0,-x3,x2]`.

Its source calls this different from Llama. The current TT attention update contract describes its HF-format rotary input in split-half terms, while TT also exposes several distinct rotary kernels. A function name containing `hf` is not enough evidence that the Command-R pair ordering is correct.

The oracle therefore carries a hostile discriminator:

- Cohere pairwise: `[1,2,3,4] -> [-2,1,-4,3]`
- split-half discriminator: `[1,2,3,4] -> [-3,-4,1,2]`

Device integration should preserve this vector before any full-model PCC claim.

### 3. GQA geometry is a real reuse seam

The issue advertises 64 Q heads and 8 KV heads at hidden size 8192, giving head dimension 128 and **8 Q heads per KV head**. Current TT attention already carries distinct `n_heads` / `n_kv_heads`, creates separate Q/K/V heads, and uses the corresponding KV cache geometry. The host oracle fixes the reference `repeat_kv` ordering: each KV head is repeated eight times, preserving source-head order.

This is a **reuse candidate**, not device validation.

### 4. Shared TT MLP already matches the required SwiGLU structure

Current TT MLP documents `w1 -> gate_proj`, `w3 -> up_proj`, `w2 -> down_proj`, defaults to SiLU, multiplies activated gate by up projection, then feeds the product to the down projection. That matches the current Cohere reference form:

`down_proj(SiLU(gate_proj(x)) * up_proj(x))`.

The oracle includes numerically stable host SiLU/SwiGLU fixtures, including a large-negative input.

### 5. Command-R logit scaling must stay explicit

Current Hugging Face Cohere applies `logits = lm_head(hidden_states) * logit_scale`. The shared TT `lm_head.py` path inspected here returns its concatenated/all-reduced linear output without a model-specific post-linear scale in that file.

A Command-R integration must therefore prove where the model's `logit_scale` is applied. If it is not already applied by a caller/model wrapper, add it explicitly. The packet deliberately accepts `logit_scale` as data rather than guessing the model's runtime value.

### 6. Source prose is not a substitute for the exact reference revision

Issue #49307 mentions Command-R-specific scaling details. Current Transformers code at the pin above uses its current Cohere attention/reference behavior. Before upstream implementation acceptance, pin the **actual model config + exact reference revision** used for PCC and derive the expected scale/rotary behavior from those bytes. Do not silently combine an older issue description with a newer reference implementation.

## Executable acceptance vectors

`command_r_host_oracle.py` has no third-party dependency. `fixture_report.json` is generated from it and captures:

- Command-R structural contract: 8192 hidden, 22528 intermediate, 40 layers, 64 Q / 8 KV, head dim 128, GQA ratio 8.
- Cohere LayerNorm vs RMSNorm hostile vector and maximum difference.
- Cohere pairwise rotary vs split-half discriminator.
- A non-commuting norm-before-RoPE vector so ordering regressions fail.
- 8-KV to 64-Q GQA expansion.
- stable SwiGLU values.
- explicit post-head logit scaling example.

## Validation

Local pre-publication validation on Python 3.13.5:

```text
python -m unittest -v tests/test_tt_metal_49307_command_r_oracle.py
Ran 10 tests in 0.001s
OK
```

Repository CI additionally runs the full test suite on Python 3.9 and 3.13. Hosted status must be read from the exact PR head; this document does not pre-claim it.

## Upstream handoff gates

Before treating a Command-R implementation as bounty-ready:

1. Freeze the exact `c4ai-command-r-v01` config/checkpoint identity and reference implementation revision used for comparison.
2. Prove Q/K **mean-centered LayerNorm** behavior across actual per-head weights; do not substitute RMSNorm.
3. Prove the Cohere even/odd rotary convention on the hostile vector for prefill and decode paths.
4. Prove 64Q/8KV mapping and cache head ordering, including tensor-parallel partitioning.
5. Prove the post-LM-head `logit_scale` location with a direct logits fixture.
6. Run per-component and per-layer reference comparisons before end-to-end text generation.
7. Only the assigned hardware-capable owner/provider path may make T3K, PCC, throughput, TTFT, acceptance, or payout claims.

## Authority / non-claims

This packet does not mutate `tenstorrent/tt-metal`, does not submit a competing model-bringup PR, does not change the issue assignment, does not contact the sponsor, does not touch payment state, and does not claim provider acceptance. It is independent validation support for the assigned implementation lane.
