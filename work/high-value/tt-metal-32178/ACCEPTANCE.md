# TT-Metal #32178 — CosyVoice host acceptance contract

Lane: `HVQ-TT-METAL-20260920-32178/COSYVOICE-INTERFACE+HOST-FIXTURE-CONTRACT`

This packet is an **independent acceptance aid**, not a competing CosyVoice bring-up and not a payout claim. The upstream issue is assigned to `ayewo`. It deliberately stops at source-pinned host contracts so the assigned implementation can consume a stable interface/fixture matrix without inheriting unmeasured Tenstorrent hardware claims.

## Canonical pins

- Tenstorrent issue: `tenstorrent/tt-metal#32178`, open, title advertises **$3000**, assignee `ayewo`.
- TT-Metal main: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`.
- CosyVoice canonical repository: `QwenAudio/CosyVoice` (the former `FunAudioLLM/CosyVoice` route redirects here).
- CosyVoice main: `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`.

Pinned source blobs:

| Source | Blob |
| --- | --- |
| `QwenAudio/CosyVoice:cosyvoice/utils/class_utils.py` | `aab83266a2644b5110aeaa64317c25b61419ba0d` |
| `QwenAudio/CosyVoice:cosyvoice/cli/model.py` | `92a15d985dfbca4bb676d3fdc02732be1d07c461` |
| `QwenAudio/CosyVoice:cosyvoice/cli/frontend.py` | `6d397cc99be417e98b8d9fc7a1438ec722312276` |
| `tenstorrent/tt-metal:models/common/models/qwen2_7b/generator.py` | `07d3ea4cb18d5a76d35ca0eb70aae52b50ffc308` |
| `tenstorrent/tt-metal:tech_reports/ttnn/TTNN-model-bringup.md` | `348bba20a98046286711f319bb1c72494997f67f` |

## Source-grounded pipeline

Current CosyVoice wires three model components in order:

`frontend -> LLM semantic tokens -> flow acoustic mel -> HiFT waveform`

`class_utils.py` distinguishes the implementations explicitly. The 300M-era shape is `TransformerLM + MaskedDiffWithXvec + HiFTGenerator`; CosyVoice2 uses `Qwen2LM + CausalMaskedDiffWithXvec + HiFTGenerator`. This packet does **not** silently substitute CosyVoice2 for the issue's requested CosyVoice-300M; Qwen2 is recorded only as a TTNN reuse precedent.

The host runtime establishes these handoff invariants:

1. LLM inference produces speech-token ids. Voice conversion is the exception: source speech tokens bypass LLM generation and directly populate the token stream.
2. Flow consumes token ids, prompt tokens, prompt acoustic features, speaker/flow embedding, and its streaming cache; it emits an 80-channel mel representation plus updated cache.
3. HiFT consumes mel frames plus cached source waveform state and emits waveform plus updated source state.
4. Streaming keeps token overlap, mel overlap, mel cache, source cache, and speech fade state across chunks. Finalization flushes remaining tokens exactly once.

## Mode fixture matrix

| Mode | Required host fixture inputs | LLM path | Special invariant |
| --- | --- | --- | --- |
| SFT | target text + speaker id | yes | speaker-derived LLM/flow embeddings remain present |
| zero-shot | target text + prompt text + prompt audio | yes | prompt speech token/features + speaker embedding are bound to the same prompt |
| cross-lingual | target text + prompt audio | yes | frontend removes prompt text from the LLM prompt surface |
| instruct | target text + speaker id + instruction text | yes | frontend removes `llm_embedding` to avoid leaking speaker identity into the instruction-conditioned LLM path |
| voice conversion | source audio/tokens + prompt audio | **no** | source speech tokens are the generated-token stream; flow/HiFT still own acoustic synthesis |

The executable verifier in this packet encodes those boundaries without importing Torch, TTNN, model weights, audio codecs, or network state.

## TTNN reuse boundary

Current TT-Metal at the pinned SHA contains a Qwen2-7B generator path with:

- `PagedKVCacheConfig`
- `LaneGroupExecutor`
- `VLLMAdapter`
- a shared Qwen2 executor/adaptor/model split.

That is useful precedent for an autoregressive semantic-token LLM carrier. It is **not evidence** that CosyVoice's exact LLM, flow decoder, HiFT vocoder, multilingual tokenizer, acoustic features, or streaming state already work on Tenstorrent.

The pinned TTNN bring-up report says to keep a Torch reference, extract the graph/module/op inventory, create per-op and per-module TTNN tests, compare module outputs (PCC), and only then optimize/profile. The assigned implementation should therefore preserve independent checkpoints at least at:

1. frontend/reference inputs -> semantic tokens,
2. semantic tokens + prompt context -> flow mel,
3. mel + source cache -> HiFT waveform,
4. full non-stream vs chunked-stream equivalence,
5. each user-visible inference mode.

## Host acceptance gates

A candidate host packet may claim **HOST_CONTRACT_GREEN** only if all of these hold:

- all five fixture modes validate;
- stage order remains frontend/LLM/flow/HiFT except the documented voice-conversion LLM bypass;
- dtype/rank labels for token, mel, prompt-feature, embedding, and waveform handoffs remain explicit;
- cross-lingual prompt-text removal and instruct-mode LLM-embedding removal are preserved;
- streaming cache/overlap constants are not collapsed into stateless chunk processing;
- voice conversion does not fabricate an LLM-generation step;
- source pins match the evidence JSON.

## Device-only gates — not satisfied here

The following stay hard-false until independently executed on the required provider hardware and reference setup:

- N150/N300 execution success;
- semantic-token accuracy against PyTorch;
- token/s throughput;
- RTF;
- WER;
- speaker similarity;
- five-language audio validity;
- device memory/sharding/KV-cache performance;
- Stage 2 or Stage 3 bounty completion.

Issue #32178 explicitly conditions payout on all three stages. This host packet grants no assignment, merge, acceptance, hardware-validation, or payment authority.

## Suggested consumption by the assigned implementation

Use the fixture IDs from `host_fixture_contract.py` as named checkpoints in the implementation PR. Bind each checkpoint to actual reference tensors and TTNN tensors when those exist. Report measured results next to the exact TT-Metal commit/hardware/model weights that produced them. Do not upgrade this packet's host-only evidence into provider acceptance.
