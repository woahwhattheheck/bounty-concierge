#!/usr/bin/env python3
"""Host-only geometry oracle for Tenstorrent tt-metal bounty #54104.

This module models only source-visible CosyVoice2 tensor/time geometry.  It does
not simulate TTNN kernels, device numerics, audio quality, latency, or RTF.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from math import prod
from typing import Iterable


@dataclass(frozen=True)
class CosyVoice2Contract:
    sample_rate: int = 24_000
    token_frame_rate: int = 25
    token_mel_ratio: int = 2
    chunk_tokens: int = 25
    upsample_rates: tuple[int, ...] = (8, 5, 3)
    upsample_kernel_sizes: tuple[int, ...] = (16, 11, 7)
    n_fft: int = 16
    hop: int = 4

    @property
    def mel_frame_rate(self) -> int:
        return self.token_frame_rate * self.token_mel_ratio

    @property
    def waveform_samples_per_mel_frame(self) -> int:
        return prod(self.upsample_rates) * self.hop

    @property
    def reference_samples_per_mel_frame(self) -> int:
        if self.sample_rate % self.mel_frame_rate:
            raise ValueError("sample_rate must be divisible by mel_frame_rate")
        return self.sample_rate // self.mel_frame_rate

    @property
    def spectral_bins(self) -> int:
        return self.n_fft // 2 + 1

    @property
    def synthesis_head_channels(self) -> int:
        # Reference conv_post emits n_fft+2: 9 magnitude + 9 phase channels.
        return self.n_fft + 2

    def validate(self) -> None:
        if self.sample_rate <= 0 or self.token_frame_rate <= 0 or self.token_mel_ratio <= 0:
            raise ValueError("rates must be positive")
        if self.n_fft <= 0 or self.n_fft % 2:
            raise ValueError("this oracle requires a positive even n_fft")
        if self.hop <= 0 or self.hop > self.n_fft:
            raise ValueError("hop must be in 1..n_fft")
        if len(self.upsample_rates) != len(self.upsample_kernel_sizes):
            raise ValueError("upsample rate/kernel lengths differ")
        if self.waveform_samples_per_mel_frame != self.reference_samples_per_mel_frame:
            raise ValueError(
                "HiFT upsample*iSTFT-hop does not match sample-rate/mel-frame-rate contract"
            )
        for stride, kernel in zip(self.upsample_rates, self.upsample_kernel_sizes):
            if stride <= 0 or kernel <= 0:
                raise ValueError("upsample strides/kernels must be positive")
            if (kernel - stride) % 2:
                raise ValueError("reference padding (kernel-stride)//2 would not be symmetric")


CONTRACT = CosyVoice2Contract()


@dataclass(frozen=True)
class StageGeometry:
    stage: int
    upsample_stride: int
    decoder_after_upsample: int
    decoder_at_fusion: int
    source_stride: int
    source_at_fusion: int


@dataclass(frozen=True)
class Geometry:
    mel_frames: int
    source_samples: int
    source_stft_frames: int
    output_samples: int
    stages: tuple[StageGeometry, ...]


def conv_transpose1d_out(
    length: int,
    *,
    stride: int,
    kernel: int,
    padding: int,
    dilation: int = 1,
    output_padding: int = 0,
) -> int:
    if min(length, stride, kernel, dilation) <= 0 or padding < 0 or output_padding < 0:
        raise ValueError("invalid ConvTranspose1d geometry")
    return (length - 1) * stride - 2 * padding + dilation * (kernel - 1) + output_padding + 1


def conv1d_out(
    length: int,
    *,
    kernel: int,
    stride: int = 1,
    padding: int = 0,
    dilation: int = 1,
) -> int:
    if min(length, kernel, stride, dilation) <= 0 or padding < 0:
        raise ValueError("invalid Conv1d geometry")
    numerator = length + 2 * padding - dilation * (kernel - 1) - 1
    if numerator < 0:
        raise ValueError("kernel does not fit the logical input")
    return numerator // stride + 1


def centered_stft_frames(signal_length: int, *, n_fft: int, hop: int) -> int:
    """torch.stft frame count for center=True and an even n_fft."""
    if signal_length <= 0 or n_fft <= 0 or n_fft % 2 or hop <= 0:
        raise ValueError("invalid centered STFT geometry")
    pad = n_fft // 2
    return (signal_length + 2 * pad - n_fft) // hop + 1


def centered_istft_default_length(frame_count: int, *, hop: int) -> int:
    """torch.istft default output length for center=True when length is omitted."""
    if frame_count <= 0 or hop <= 0:
        raise ValueError("invalid iSTFT geometry")
    return (frame_count - 1) * hop


def _source_stage_strides(rates: Iterable[int]) -> tuple[int, ...]:
    # Reference:
    # downsample_rates = [1] + upsample_rates[::-1][:-1]
    # downsample_cum_rates = np.cumprod(downsample_rates)
    # iterate downsample_cum_rates[::-1]
    reversed_rates = list(reversed(tuple(rates)))
    base = [1] + reversed_rates[:-1]
    cumulative: list[int] = []
    running = 1
    for value in base:
        running *= value
        cumulative.append(running)
    return tuple(reversed(cumulative))


def geometry(mel_frames: int, contract: CosyVoice2Contract = CONTRACT) -> Geometry:
    contract.validate()
    if mel_frames <= 0:
        raise ValueError("mel_frames must be positive")

    source_samples = mel_frames * contract.waveform_samples_per_mel_frame
    source_stft_frames = centered_stft_frames(
        source_samples, n_fft=contract.n_fft, hop=contract.hop
    )

    decoder_length = mel_frames
    source_strides = _source_stage_strides(contract.upsample_rates)
    stages: list[StageGeometry] = []

    for index, (stride, kernel, source_stride) in enumerate(
        zip(contract.upsample_rates, contract.upsample_kernel_sizes, source_strides)
    ):
        padding = (kernel - stride) // 2
        decoder_length = conv_transpose1d_out(
            decoder_length, stride=stride, kernel=kernel, padding=padding
        )
        decoder_after_upsample = decoder_length

        # Reference applies ReflectionPad1d((1, 0)) only at the final stage,
        # before source fusion and conv_post.
        decoder_at_fusion = decoder_length + (1 if index == len(contract.upsample_rates) - 1 else 0)

        if source_stride == 1:
            source_at_fusion = conv1d_out(
                source_stft_frames, kernel=1, stride=1, padding=0
            )
        else:
            source_at_fusion = conv1d_out(
                source_stft_frames,
                kernel=source_stride * 2,
                stride=source_stride,
                padding=source_stride // 2,
            )

        stages.append(
            StageGeometry(
                stage=index,
                upsample_stride=stride,
                decoder_after_upsample=decoder_after_upsample,
                decoder_at_fusion=decoder_at_fusion,
                source_stride=source_stride,
                source_at_fusion=source_at_fusion,
            )
        )

        if decoder_at_fusion != source_at_fusion:
            raise AssertionError(
                f"stage {index} source/decoder mismatch: "
                f"{source_at_fusion} != {decoder_at_fusion}"
            )

        # The extra reflected frame exists only for final fusion/synthesis.
        if index == len(contract.upsample_rates) - 1:
            decoder_length = decoder_at_fusion

    output_samples = centered_istft_default_length(
        decoder_length, hop=contract.hop
    )
    expected = mel_frames * contract.reference_samples_per_mel_frame
    if output_samples != expected:
        raise AssertionError(f"output sample mismatch: {output_samples} != {expected}")

    return Geometry(
        mel_frames=mel_frames,
        source_samples=source_samples,
        source_stft_frames=source_stft_frames,
        output_samples=output_samples,
        stages=tuple(stages),
    )


def overlap_state() -> dict[str, int]:
    """Minimum structural state implied by n_fft=16/hop=4 overlap-add.

    This is a geometry lower bound, not a prescription for a TTNN implementation.
    An implementation may carry equivalent spectral/frame state instead.
    """
    CONTRACT.validate()
    overlap_samples = CONTRACT.n_fft - CONTRACT.hop
    prior_frames = overlap_samples // CONTRACT.hop
    concurrent_frames = CONTRACT.n_fft // CONTRACT.hop
    return {
        "overlap_samples": overlap_samples,
        "prior_spectral_frames": prior_frames,
        "concurrent_window_count": concurrent_frames,
        "center_trim_samples_each_side": CONTRACT.n_fft // 2,
    }


def canonical_fixture(mel_frames: int) -> dict:
    g = geometry(mel_frames)
    return {
        "mel_frames": g.mel_frames,
        "source_samples": g.source_samples,
        "source_stft_frames": g.source_stft_frames,
        "output_samples": g.output_samples,
        "stages": [asdict(stage) for stage in g.stages],
    }


def acceptance_document() -> dict:
    CONTRACT.validate()
    chunk_mel_frames = CONTRACT.chunk_tokens * CONTRACT.token_mel_ratio
    chunk = geometry(chunk_mel_frames)
    return {
        "contract": {
            **asdict(CONTRACT),
            "mel_frame_rate": CONTRACT.mel_frame_rate,
            "waveform_samples_per_mel_frame": CONTRACT.waveform_samples_per_mel_frame,
            "reference_samples_per_mel_frame": CONTRACT.reference_samples_per_mel_frame,
            "spectral_bins": CONTRACT.spectral_bins,
            "synthesis_head_channels": CONTRACT.synthesis_head_channels,
        },
        "streaming_state_lower_bound": overlap_state(),
        "canonical_fixtures": [
            canonical_fixture(n) for n in (1, 2, 7, chunk_mel_frames, 63)
        ],
        "canonical_chunk": {
            "tokens": CONTRACT.chunk_tokens,
            "mel_frames": chunk_mel_frames,
            "output_samples": chunk.output_samples,
            "seconds_at_24khz": chunk.output_samples / CONTRACT.sample_rate,
        },
    }


def main() -> int:
    print(json.dumps(acceptance_document(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
