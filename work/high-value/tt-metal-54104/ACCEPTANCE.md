# Tenstorrent tt-metal #54104 — current-source CosyVoice2 streaming/iSTFT acceptance packet

Status: independent host/reference artifact for the **already-assigned** implementation lane. This packet does **not** claim issue assignment, a competing upstream implementation, Tenstorrent hardware execution, numerical device parity, audio quality, latency/RTF, bounty ownership, merge authority in `tenstorrent/tt-metal`, or payout.

## Exact source fence

Captured 2026-09-20 UTC.

### Tenstorrent

- Issue `tenstorrent/tt-metal#54104`: **[Bounty $2000] CosyVoice2 (HiFT Vocoder + iSTFT + Streaming Pipeline) bring up using TTNN APIs**; open, `bounty` + `model bringup`, assigned to `Sedherthe`.
- Literal upstream `main`: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`.
- `models/tt_dit/models/audio_vae/bwe_ltx.py` blob `776f4dae4c934e46ad449ee17cb83b1e9da3dd21`.
- `models/tt_dit/models/audio_vae/vocoder_ltx.py` blob `bdfdc58fb8363932a169d03854c5289cf6aac9ec`.
- `models/experimental/pi0/tt/ttnn_pi0_model.py` blob `f07660b78a378b3f5fd97227a388a49376f689a9`.
- `models/tt_transformers/tt/model_config.py` blob `33f196f5f8479000c158bc6d01378bbc3b37789b`.

### CosyVoice2 reference

The current canonical GitHub repository resolves as `QwenAudio/CosyVoice`.

- Literal reference `main`: `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`.
- `examples/libritts/cosyvoice2/conf/cosyvoice2.yaml` blob `8a4db63b1e3a9be36d001df61109074f002813a3`.
- `cosyvoice/hifigan/generator.py` blob `bbc2a2112bfd260963765af33760c95c3161fe14`.

These pins are evidence inputs, not a promise that either repository will retain the same topology.

## Current-source correction: the advertised STFT precedent moved

The issue text says to mirror the existing forward-STFT matmul pattern in `vocoder_ltx.py`. That is stale at the pinned current main.

Current `vocoder_ltx.py` is the LTX-2 BigVGAN-v2/AMP1 vocoder. The forward-STFT implementation is now `_STFTFn` in `bwe_ltx.py`. More importantly, `_STFTFn` is **not an inverse-transform implementation**:

- it does a host-side waveform `unfold`, uploads windows, and performs a device `matmul` against `forward_basis`;
- it returns magnitude only;
- `_prepare_torch_state()` explicitly drops `inverse_basis`;
- its own docstring says the iSTFT path and phase are dropped;
- it therefore proves that TTNN matmul can carry the DFT-basis multiply, but it does **not** prove iSTFT synthesis, complex recombination, overlap-add normalization, or streaming boundary continuity.

A #54104 carrier should cite `bwe_ltx.py` as the current forward-transform precedent and implement/validate the missing inverse half explicitly. Treating current `_STFTFn` as an already-present iSTFT would be a false-green acceptance condition.

## Exact CosyVoice2 geometry

The pinned CosyVoice2 YAML binds:

- sample rate: `24000`;
- token frame rate: `25 Hz`;
- token→mel ratio: `2`, therefore `50` mel frames/s;
- streaming `chunk_size`: `25` tokens, therefore `50` mel frames / exactly one second before lookahead/context handling;
- HiFT upsample rates: `[8, 5, 3]`;
- kernels: `[16, 11, 7]`;
- iSTFT: `n_fft=16`, `hop_len=4`;
- source-resblock kernels: `[7, 7, 11]`;
- Qwen2 LLM → causal flow-matching decoder → HiFT.

The fundamental sample-rate identity is:

`8 * 5 * 3 * 4 = 480 samples/mel-frame = 24000 / 50`.

A final implementation must preserve this identity or intentionally change both reference/config and validation together.

### Synthesis head contract

Pinned `HiFTGenerator` emits `n_fft + 2 = 18` channels from `conv_post`:

- first `n_fft/2 + 1 = 9` channels → `exp(...)` magnitude;
- remaining `9` channels → `sin(...)` phase;
- reference reconstructs `real = magnitude*cos(phase)` and `imag = magnitude*sin(phase)`;
- `torch.istft(..., n_fft=16, hop_length=4, win_length=16, hann_window)` performs synthesis;
- output is clamped to `[-audio_limit, +audio_limit]`.

An acceptance test that feeds arbitrary complex bins directly to a proposed iSTFT is useful for the op, but it is not enough for model parity: the model-level test must also preserve this **18-channel magnitude/phase head contract** and its nonlinearities.

## Stage-by-stage shape theorem

`streaming_contract.py` derives the exact integer geometry from the reference formulas, with no NumPy/PyTorch dependency.

For `T` mel frames:

1. NSF/F0 source is upsampled by `8*5*3*4 = 480` → `480T` waveform samples.
2. Reference centered STFT (`n_fft=16`, hop `4`) produces `120T + 1` spectral frames.
3. Decoder transposed-convolution stages produce exactly:
   - stage 0: `8T`,
   - stage 1: `40T`,
   - stage 2 before final reflection pad: `120T`.
4. Final `ReflectionPad1d((1,0))` makes stage-2 fusion length `120T + 1`.
5. Source-downsample strides derived by the reference are `[15, 3, 1]`; from `120T+1` source-STFT frames they produce exactly `[8T, 40T, 120T+1]`.
6. Therefore every source-fusion add is length-aligned **without ad-hoc cropping**.
7. Centered iSTFT over `120T+1` frames with hop `4` and no explicit `length` returns `(120T+1-1)*4 = 480T` samples.

For one mel frame, the exact proof vector is:

| item | length |
| --- | ---: |
| source waveform | 480 |
| source STFT | 121 |
| decoder stage 0 / source stage 0 | 8 / 8 |
| decoder stage 1 / source stage 1 | 40 / 40 |
| decoder stage 2 before pad | 120 |
| final decoder fusion / source stage 2 | 121 / 121 |
| centered iSTFT output | 480 |

For the configured 25-token streaming chunk: `25 tokens → 50 mel frames → 24,000 samples → 1.0 s`.

The host oracle verifies the same identities for 1, 2, 3, 7, 31, 50, 63, and 127 mel-frame lengths in its regression suite.

## Streaming boundary contract

A whole-utterance `torch.istft` match is **not** sufficient for Stage 2/3 acceptance.

With `n_fft=16` and hop `4`, adjacent synthesis frames overlap by `12` samples and four windows can contribute at once. A streaming implementation must preserve an equivalent of:

- `12` output samples of overlap-add numerator/normalization state, or a mathematically equivalent representation;
- the preceding `3` spectral frames that still overlap the next hop, if state is kept spectrally rather than in the OLA accumulator;
- center-mode boundary semantics (`n_fft/2 = 8` samples per edge in the reference transform pair);
- no double-window, dropped-window, duplicated hop, or reset of normalization at chunk boundaries.

These are **structural lower bounds**, not a required internal TTNN data structure.

### Required hostile streaming tests

For the exact final candidate, compare concatenated streaming output against the same revision's whole-utterance reference using deterministic spectra and model-derived head outputs:

1. split immediately after 1 mel frame;
2. split at configured 50-mel-frame chunk;
3. uneven chunks `7 + 13 + 31 + remainder`;
4. zero/near-zero magnitude around a boundary;
5. phase changes immediately before/after the boundary;
6. an impulse whose Hann support crosses the boundary;
7. consecutive chunks where the second chunk has a different shape bucket;
8. finalize vs non-finalize path: only the final call may apply terminal center/tail handling.

The test must compare the **full concatenated waveform**, including a focused window around each seam. Merely checking each chunk independently can green a discontinuous implementation.

## Transferable TTNN precedents — and their limits

### Forward transform

`bwe_ltx.py::_STFTFn` is useful for:

- explicit real/imag basis layout;
- FP32 matmul configuration;
- conversion between logical non-tile-aligned frequency slices and TTNN layouts.

It is not proof for inverse synthesis because inverse basis and phase are deliberately discarded.

### Euler integration

`models/experimental/pi0/tt/ttnn_pi0_model.py` contains an on-device Euler update:

`x_next = x + velocity * dt`

with the denoising loop resident on device. That is a useful control-flow/data-residency precedent for CosyVoice2's Euler-configured flow matcher. It does **not** establish CosyVoice2 decoder shape, conditioning, scheduler, or numerical parity.

### Qwen precedent

Current `tt_transformers` has explicit Qwen2.5 precision/configuration paths. That is a reuse starting point, not evidence that CosyVoice2's Qwen2-0.5B can be loaded unchanged. Acceptance must pin the exact reference model config and validate at minimum embedding/vocab shape, head layout, RoPE configuration, KV-cache semantics, prefill/decode token positions, and token logits/selected-token parity for the intended checkpoint.

## Independent iSTFT acceptance ladder

### Layer A — host algebra

Before hardware claims:

- construct Hermitian-consistent spectra for real output;
- test DC-only, Nyquist-only, single interior bin, random bounded spectra, zero spectrum, and phase rotations;
- validate Hann-window overlap/add against `torch.istft` using the exact `n_fft=16`, hop `4`, center/window semantics;
- include multiple frame counts, especially 1, 2, 4, 5, 121, and the configured 6001-frame one-second chunk case (`120*50 + 1`);
- compare before output clamp so synthesis error is not hidden.

### Layer B — TTNN op/device

On each claimed architecture:

- exact candidate SHA, firmware/KMD/runtime identity;
- FP32 and any deliberately supported lower-precision routes separated;
- device output vs PyTorch reference with tolerances justified by the actual arithmetic path;
- program-cache reuse with at least two frame counts;
- no hidden host inverse-transform fallback;
- memory/layout evidence for non-tile-aligned logical `9` frequency bins;
- prove both real and imaginary basis paths execute;
- verify OLA accumulator/normalizer state survives chunk boundaries.

### Layer C — HiFT component

- F0 predictor parity independently;
- NSF/SineGen2 parity with deterministic/random-seed controls;
- all three `[8,5,3]` upsample stages;
- all three source branches, including exact `[15,3,1]` source-downsample geometry;
- 18-channel synthesis-head parity before iSTFT;
- waveform parity after iSTFT and clamp.

### Layer D — flow + LLM + end-to-end

Only after A–C:

- Qwen2 token/logit acceptance;
- flow decoder output at fixed seed/timestep schedule;
- whole-utterance HiFT;
- streaming equality/continuity;
- then provider-required token accuracy, WER/speaker similarity, latency, time-to-first-packet, and RTF on the exact hardware/revision.

Host geometry success must never be relabeled as those provider results.

## Commands

From this directory:

```bash
python3 test_streaming_contract.py
python3 -O test_streaming_contract.py
python3 streaming_contract.py > /tmp/tt54104-geometry.json
```

Source-publish proof on the authored bytes:

- `python3 test_streaming_contract.py` → **9/9 PASS**
- `python3 -O test_streaming_contract.py` → **9/9 PASS**
- `python3 streaming_contract.py` → exit `0`, deterministic JSON emitted

The tests are stdlib-only and make no TTNN/device claim.

## Final merge/review fence for an upstream implementation

Before accepting an assigned #54104 source carrier:

1. re-read literal `tenstorrent/tt-metal/main` and the exact candidate head;
2. re-pin the current CosyVoice reference/config used by the candidate;
3. ensure any forward-STFT/iSTFT reuse is still source-accurate — especially the current `bwe_ltx.py` vs stale `vocoder_ltx.py` location;
4. prove source-fusion lengths for the exact configured `[8,5,3]` path rather than relying on one example;
5. run the iSTFT ladder above on the exact head;
6. run full component/end-to-end provider validation on hardware;
7. publish measurements with exact device/runtime/toolchain provenance;
8. preserve the bounty assignee/provider process rather than treating this independent artifact as an assignment or payment entitlement.
