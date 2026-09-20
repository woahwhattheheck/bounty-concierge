# tt-metal #54104 — iSTFT host oracle and streaming overlap-add contract

Lane: ZZ-Sol-Capstan-907 / GPT-5.6 Sol
Date: 2026-09-19 EDT
Canonical issue: `tenstorrent/tt-metal#54104` — `[Bounty $2000] CosyVoice2 (HiFT Vocoder + iSTFT + Streaming Pipeline) bring up using TTNN APIs`

## Authority and ownership

The issue is OPEN and assigned to `Sedherthe`. The $2,000 title is source-verifiable, but this packet claims **no implementation ownership, bounty entitlement, TT hardware result, PCC, latency, RTF, merge, or payment**. It is an independent host-numerical acceptance artifact for the assigned implementation.

## Exact source pins

- `tenstorrent/tt-metal main`: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`
- forward-STFT precedent: `models/tt_dit/models/audio_vae/bwe_ltx.py` blob `776f4dae4c934e46ad449ee17cb83b1e9da3dd21`
- existing LTX audio test surface: `models/tt_dit/tests/models/ltx/test_audio_ltx.py` blob `efa66a87662b4f384277f2e852d5402d05e8a639`

The current `_STFTFn` is already a causal host-unfold + device-matmul implementation. It uses `left_pad = win_length - hop_length`, stores a static `forward_basis`, and deliberately drops `inverse_basis` in `_prepare_torch_state`. The current LTX tests use `filter_length=512`, `hop_length=80`, `win_length=512`, and the torch reference object still carries both forward and inverse bases. That makes the inverse transform a real, source-local missing seam rather than a speculative rewrite.

## Oracle contract

`istft_oracle.py` mirrors the bounty's proposed matmul-centric implementation shape without TTNN:

1. one-sided complex coefficients are represented as `real[0:n_freq] + imag[0:n_freq]`;
2. a static real-valued inverse-DFT basis maps those coefficients to a time-domain frame;
3. the synthesis window is applied;
4. frames are overlap-added at `hop_length`;
5. output is divided by the accumulated `window**2` denominator;
6. any requested output sample whose denominator is effectively zero fails closed;
7. streaming emission releases only samples that no future frame can modify;
8. `left_pad` and exact requested `length` are explicit inputs rather than implicit slicing assumptions.

This is the minimum numerical contract a TTNN iSTFT should preserve before device/PCC/performance claims are meaningful.

## Important streaming boundary found

For a causal Hann configuration, merely left-padding the signal is insufficient to guarantee a reconstructible final requested sample. The Hann endpoint is zero. If the last requested sample is covered only by that endpoint, the overlap denominator is zero and a naive flush either divides by zero or silently fabricates the tail.

The regression therefore requires an **explicit terminal zero-padded flush frame (or a mathematically equivalent tail-coverage mechanism)**. In the host oracle, `flush_right_pad=True` appends `win_length - hop_length` zeros before final framing. The unpadded-Hann_tail test intentionally fails closed.

For the current LTX geometry (`win=512`, `hop=80`), an implementation must document how it guarantees equivalent terminal overlap coverage during whole-utterance and chunked streaming synthesis.

## Executed host proof

Exact local source bytes were executed with Python 3.13.5:

```text
python3 -m py_compile istft_oracle.py tests/test_tt_metal_54104_istft_oracle.py   PASS
python3 -m unittest -v tests.test_tt_metal_54104_istft_oracle                   12/12 PASS
python3 -O -m unittest -v tests.test_tt_metal_54104_istft_oracle                12/12 PASS
```

Both files also parse under Python 3.9 grammar via `ast.parse(..., feature_version=(3, 9))`.

Optional CPU cross-check on PyTorch 2.10.0+cpu used a rectangular `n_fft=8`, `hop=4`, `center=False` case; host-oracle output agreed with `torch.istft(torch.stft(...))` to max absolute error `3.3306690738754696e-16`.

## Regression panel

The checked-in test module covers:

- even-FFT one-sided inverse-basis round trip;
- odd-FFT one-sided inverse-basis round trip;
- causal Hann overlap-add reconstruction with terminal tail coverage;
- arbitrary streaming chunk partitions equal full reconstruction;
- one-frame-at-a-time streaming equals bulk streaming;
- exact output-length trimming;
- zero/unsafe denominator fail-closed behavior;
- unpadded causal Hann tail fail-closed behavior;
- invalid `hop_length > win_length` rejection;
- streaming spectral-shape mismatch rejection;
- flush idempotency and push-after-flush rejection;
- inverse-basis shape mismatch rejection.

## Adoption checklist for the assigned TTNN implementation

Before calling iSTFT accepted:

- preserve the exact inverse-basis coefficient convention and scaling, including DC/Nyquist handling;
- pin whether basis rows are `win_length` or `n_fft` and reject incompatible checkpoint shapes;
- validate device output numerically against a trusted host reference across random, impulse, sinusoid, and adversarial phase inputs;
- prove window-square normalization rather than raw overlap-add amplitude drift;
- define `center`, left/right padding, output-length, and terminal flush semantics explicitly;
- prove streaming chunk partition invariance: the same frames split into different push batches must produce the same waveform;
- include a zero-denominator guard rather than silently emitting NaN/Inf/zero;
- add current `512/80/512` LTX geometry plus the CosyVoice2 target geometry;
- only after numerical correctness, measure PCC/quality, time-to-first-packet, and RTF on actual Tenstorrent hardware.

## Non-goals

This packet does not implement a TTNN kernel/op, the HiFT vocoder, F0/NSF modules, Qwen2, flow matching, hardware streaming, or performance tuning. It is a bounded host acceptance oracle intended to reduce numerical ambiguity for the assigned bounty owner.
